import argparse
import datetime
import logging
import plistlib
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from jinja2 import Environment, PackageLoader


def _info_plist_path() -> Path:
    wf_dir = Path.cwd() / "Workflow"
    info_plist_path = wf_dir / "info.plist"
    return info_plist_path


def _ensure_no_local_changes(
    fn: Callable[[argparse.Namespace], None],
) -> Callable[[argparse.Namespace], None]:
    """Ensure that there are no changes to the local directory"""

    def decorator(args: argparse.Namespace) -> None:
        git_status_command = ["git", "status", "-s"]
        if subprocess.check_output(git_status_command).decode("utf-8").strip():
            logging.critical("Local changes detected. Ensure git history has all changes committed.")
            exit(1)

        fn(args)

    return decorator


def _must_be_run_from_workflow_project_root(
    fn: Callable[[argparse.Namespace], None],
) -> Callable[[argparse.Namespace], None]:
    """Validates that the command is run from a directory that contains a workflow"""

    def decorator(args: argparse.Namespace):
        if not _info_plist_path().exists():
            logging.critical("Cannot find workflow. You need to run this command from the root of the project")
            exit(1)

        fn(args)

    return decorator


def _get_sync_directory() -> Optional[Path]:
    """
    :return: The path to Alfred's sync directory
    """
    prefs_path = Path.home() / "Library" / "Preferences" / "com.runningwithcrayons.Alfred-Preferences.plist"

    if not prefs_path.exists():
        raise ValueError("Alfred doesn't appear to be installed")

    with prefs_path.open("rb") as f:
        pl = plistlib.load(f)

    if "syncfolder" not in pl:
        logging.debug("Alfred's synchronisation directory not set")
        return None

    sync_dir = Path(pl["syncfolder"]).expanduser()

    if not sync_dir.exists():
        raise OSError("Cannot find workflow directory")

    return sync_dir.expanduser()


def _get_workflows_directory() -> Path:
    """
    Get the directory where Alfred stores workflows

    Finds the Alfred.alfredpreferences dir either in the sync location set in the Alfred settings or in the default
    location.

    :return: The path to the directory with Alfred's workflows
    """

    sync_dir = _get_sync_directory()
    prefs_dir = sync_dir or Path.home() / "Library" / "Application Support" / "Alfred/"

    return prefs_dir / "Alfred.alfredpreferences" / "workflows"


def _make_plist(
    name: str,
    keyword: str,
    bundle_id: str,
    author: Optional[str],
    website: Optional[str],
    description: Optional[str],
) -> dict:
    """
    Create a dictionary representation of the info.plist file describing the workflow

    :param name:
        The name of the workflow
    :param keyword:
        The keyword to trigger the workflow
    :param bundle_id:
        The bundle ID to identify the workflow. Should use reverse-DNS notation
    :param author:
        The name of the author
    :param website:
        The website of the workflow
    :param description:
        The description of the workflow. Will be shown to the user when importing
    :return: a dictionary representation of the info.plist file
    """
    script_uuid = str(uuid4())
    clipboard_uuid = str(uuid4())

    return {
        "name": name,
        "description": description or "",
        "bundleid": bundle_id,
        "createdby": author or "",
        "connections": {script_uuid: [{"destinationuid": clipboard_uuid}]},
        "uidata": [],
        # Environment variables
        # Add the vendored directory to the PYTHONPATH so that we're also searching there for dependencies
        "variables": {"PYTHONPATH": ".:vendored"},
        # The workflow version
        "version": "0.0.0",
        # The contact website
        "webaddress": website or "",
        "objects": [
            {
                "uid": clipboard_uuid,
                "type": "alfred.workflow.output.clipboard",
                "config": {"clipboardtext": "{query}"},
            },
            {
                "uid": script_uuid,
                "type": "alfred.workflow.input.scriptfilter",
                "config": {
                    "keyword": keyword,
                    "scriptfile": "workflow.py",
                    # Keyword should be followed by whitespace
                    "withspace": True,
                    # Argument optional
                    "argumenttype": 1,
                    # Placeholder title
                    "title": "Search",
                    # "Please wait" subtext
                    "runningsubtext": "Loading...",
                    # External script
                    "type": 8,
                    # Terminate previous script
                    "queuemode": 2,
                    # Always run immediately for first typed character
                    "queuedelayimmediatelyinitially": True,
                    # Don't set argv when empty
                    "argumenttreatemptyqueryasnil": True,
                },
            },
        ],
    }


def _zip_dir(directory: Path, output_file: Path):
    """
    Zip the contents of the provided directory recursively

    :param directory: The directory to compress
    :param output_file: The target file
    """

    with ZipFile(output_file, "w", ZIP_DEFLATED) as zip_file:
        for entry in directory.rglob("**/*"):
            if entry.is_file():
                logging.debug("Adding to package: %s", entry)
                zip_file.write(entry, entry.relative_to(directory))
    logging.info("Produced package at %s", output_file)


def find_workflow_link(target: Path) -> Optional[Path]:
    """
    Finds a link to the workflow in Alfred's workflows directory

    :param target: The path to the workflow we're looking for
    :return: The path if found; `None` otherwise
    """
    target = target.expanduser()
    workflows = _get_workflows_directory()

    for wf in workflows.iterdir():
        if wf.is_symlink() and wf.readlink().expanduser() == target:
            return wf

    return None


def new(args: argparse.Namespace):
    """
    Entry point for the `new` command. Creates a new Alfred workflow.

    This creates a directory of the name in the `name` argument and links it into Alfred's workflows directory. The
    workflow shows in the Alfred Preferences app and can still be easily edited with an external editor.

    ```
    usage: pyfred new [-h] -k KEYWORD -b BUNDLE_ID --author AUTHOR
                      [--website WEBSITE] [--description DESCRIPTION]
                      [--git | --no-git] [--link | --no-link]
                      [--vendor | --no-vendor]
                      name

    positional arguments:
      name                  Name of the new workflow

    options:
      -h, --help            show this help message and exit
      -k KEYWORD, --keyword KEYWORD
                            The keyword to trigger the workflow
      -b BUNDLE_ID, --bundle-id BUNDLE_ID
                            The bundle identifier, usually in reverse DNS notation
      --author AUTHOR       Name of the author
      --website WEBSITE     The workflow website
      --description DESCRIPTION
                            A description for the workflow
      --git, --no-git       Whether to create a git repository
      --link, --no-link     Create a symbolic link to this workflow
      --vendor, --no-vendor
                            Install workflow dependencies
    ```
    """  # noqa: E501
    name = args.name
    logging.info("Creating new workflow: %s", name)

    root_dir = Path.cwd().joinpath(name)
    wf_dir = root_dir.joinpath("Workflow")

    context = {
        "year": datetime.datetime.now().year,
        "system_python_version": subprocess.check_output(
            ["/usr/bin/python3", "-c", "print(__import__('platform').python_version())"]
        )
        .decode("utf-8")
        .strip(),
        **vars(args),
    }
    try:
        logging.debug("Copying template")
        env = Environment(loader=PackageLoader("pyfred", "template"))
        logging.debug("Generating templates from %s to %s", env.list_templates(), root_dir)
        for t in [t for t in env.list_templates() if "__pycache__" not in t]:
            tmp = env.get_template(t)
            outfile = root_dir.joinpath(t)
            outfile.parent.mkdir(parents=True, exist_ok=True)
            with open(outfile, "w") as fd:
                fd.write(tmp.render(context))
    except OSError as e:
        logging.error("Cannot create workflow: %s", e)
        exit(1)

    wf_file_path = wf_dir.joinpath("workflow.py")

    logging.debug("Adding +x permission to workflow")
    wf_file_path.chmod(wf_file_path.stat().st_mode | stat.S_IEXEC)

    if args.git:
        logging.debug("Initialising git repository")
        if subprocess.call(["git", "init", name]) != 0:
            logging.warning("Failed to create git repository. Ignoring.")

    logging.debug("Creating info.plist")
    with wf_dir.joinpath("info.plist").open(mode="xb") as f:
        plistlib.dump(
            _make_plist(
                name=name,
                keyword=args.keyword,
                bundle_id=args.bundle_id,
                author=args.author,
                website=args.website,
                description=args.description,
            ),
            f,
            sort_keys=True,
        )
    if args.vendor:
        _vendor(root_dir, upgrade=False)
    if args.link:
        _link(relink=True, same_path=False, wf_dir=wf_dir)


@_must_be_run_from_workflow_project_root
def link(args: argparse.Namespace):
    """
    Entry point for the `link` command. Links or relinks the workflow into Alfred's workflows directory.

    ```
    usage: pyfred link [-h] [--relink | --no-relink] [--same-path | --no-same-path]

    options:
      -h, --help            show this help message and exit
      --relink, --no-relink
                            Whether to delete (if exists) and recreate the link (default: False)
      --same-path, --no-same-path
                            Whether to reuse (if exists) the previous path for the link (default: False)
    ```
    """
    try:
        _link(
            relink=args.relink,
            same_path=args.same_path,
            wf_dir=Path.cwd().joinpath("Workflow"),
        )
    except ValueError as e:
        logging.error("Error creating link: %s", e)
        exit(1)


def _link(relink: bool, same_path: bool, wf_dir: Path):
    """
    Create a link to the workflow in Alfred's workflows directory

    :param relink:
        Whether to recreate the link if it exists
    :param same_path:
        Whether to reuse the same link if one exists
    :param wf_dir:
        The directory to link to
    :return:
    """
    if not wf_dir.exists():
        raise ValueError(f"{wf_dir} doesn't exist")

    if not wf_dir.is_dir():
        raise ValueError(f"{wf_dir} is not a directory")

    existing_link = find_workflow_link(wf_dir)

    if existing_link:
        if not relink:
            logging.debug("Found link: %s", existing_link)
            return

        logging.debug("Removing existing link: %s", existing_link)
        existing_link.unlink()

    logging.info("Creating link to workflow directory %s", wf_dir)

    if same_path and existing_link:
        source = existing_link
    else:
        workflow_id = str(uuid4()).upper()
        source = _get_workflows_directory().joinpath(f"user.workflow.{workflow_id}")

    logging.debug("Creating link: %s", source)
    source.symlink_to(wf_dir)

    if not source.exists():
        logging.error("Error linking from %s to %s", source, wf_dir)
        source.unlink(missing_ok=True)


@_must_be_run_from_workflow_project_root
def vendor(args: argparse.Namespace):
    """
    Entry point for the `vendor` command

    Downloads dependencies specified in the `requirements.txt` file into the workflow's `vendored` directory.
    This way, the dependencies don't need to be installed into the system Python interpreter.

    The workflow sets the `PYTHONPATH` environment variable to `.:vendored`, making the interpreter search for
    dependencies in that directory, in addition to the workflow directory.

    ```
    usage: pyfred vendor [-h] [--upgrade | --no-upgrade]

    options:
      -h, --help            show this help message and exit
      --upgrade, --no-upgrade
                            Whether to pass `--upgrade` to `pip install` when vendoring (default: False)
    ```
    """
    _vendor(root_path=Path.cwd(), upgrade=args.upgrade)


def _vendor(root_path: Path, upgrade: bool) -> bool:
    """
    Download dependencies from `requirements.txt`

    :param root_path: The root path of the workflow project
    :param upgrade: Whether to pass `--upgrade` to `pip install`
    :return: whether the download was successful
    """

    vendored_path = root_path / "Workflow" / "vendored"
    vendored_path.mkdir(parents=True, exist_ok=True)

    import subprocess

    pip_command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        f"{root_path}/requirements.txt",
        f"--target={vendored_path}",
    ]

    if upgrade:
        pip_command.append("--upgrade")

    logging.debug("Running pip: python %s", " ".join(pip_command[1:]))

    return subprocess.call(pip_command) == 0


@_ensure_no_local_changes
@_must_be_run_from_workflow_project_root
def release(args: argparse.Namespace):
    # Update version number in info.plist
    plist_path = _info_plist_path()
    with plist_path.open("rb") as f:
        pl = plistlib.load(f)

    pl["version"] = args.version
    with plist_path.open(mode="wb") as f:
        plistlib.dump(
            pl,
            f,
            sort_keys=True,
        )

    # commit changes to git
    commit_command = ["git", "commit", "-a", "-m", f"release {args.version}"]
    if subprocess.call(commit_command) != 0:
        logging.error("Error committing changes")
        exit(1)

    # create annotated tag with version
    tag_command = ["git", "tag", "-a", args.version, "-m", args.version]
    if subprocess.call(tag_command) != 0:
        logging.error("Error tagging changes")
        exit(1)

    # push changes & tag
    push_command = ["git", "push", "origin", "--follow-tags"]
    if subprocess.call(push_command) != 0:
        logging.error("Error pushing changes")
        exit(1)


@_must_be_run_from_workflow_project_root
def package(args: argparse.Namespace):
    """
    Entry point for the `package` command. Creates a package for distribution.

    Packages the workflow into a `workflow.alfredworkflow` file in the `dist` directory.
    Users can import the package by double-clicking the file.

    ```
    usage: pyfred package [-h] --name NAME

    options:
      -h, --help   show this help message and exit
      --name NAME  The name of the workflow file
    ```
    """
    root_dir = Path.cwd()

    has_requirements = root_dir.joinpath("requirements.txt").exists()
    logging.debug("requirements.txt exists %s", has_requirements)
    if has_requirements:
        if not _vendor(root_dir, upgrade=True):
            logging.error("Failed to download dependencies. Exiting")
            exit(1)

    output = root_dir / "dist"
    output.mkdir(exist_ok=True)

    _zip_dir(root_dir / "Workflow", output / f"{args.name}.alfredworkflow")


@_must_be_run_from_workflow_project_root
def version(args: argparse.Namespace) -> None:
    with _info_plist_path().open("rb") as f:
        pl = plistlib.load(f)

    print(pl["version"])


@_must_be_run_from_workflow_project_root
def name(args: argparse.Namespace) -> None:
    with _info_plist_path().open("rb") as f:
        pl = plistlib.load(f)

    print(pl["name"])


@_must_be_run_from_workflow_project_root
def show_link(args: argparse.Namespace) -> None:
    """
    Entry point for the `show-link` command. Displays the link in the installed workflows directory for this project.

    ```
    usage: pyfred show-link [-h]

    options:
      -h, --help  show this help message and exit
    ```
    """
    wf_dir = Path.cwd().joinpath("Workflow")
    link_path = find_workflow_link(wf_dir)

    if link_path:
        print(link_path)
    else:
        logging.error("No workflow link found. Use 'pyfred link' to create one.")
        sys.exit(1)


def _cli():
    """
    The entry point for the CLI.

    ```
    usage: pyfred [-h] {new,vendor,link,package,version,name,show-link} ...

    Build Python workflows for Alfred with ease

    positional arguments:
      {new,vendor,link,package,version,name,show-link}
        new                 Create a new workflow
        vendor              Install workflow dependencies
        link                Create a symbolic link to this workflow in Alfred
        package             Package the workflow for distribution
        version             Display the version of the workflow
        name                Display the name of the workflow
        show-link           Display the link in the installed workflows directory for this project

    options:
      -h, --help            show this help message and exit
      --debug, --no-debug   Whether to enable debug logging (default: False)
    ```

    """
    parser = argparse.ArgumentParser(prog="pyfred", description="Build Python workflows for Alfred with ease")
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to enable debug logging",
    )
    subparsers = parser.add_subparsers(required=True)

    new_parser = subparsers.add_parser("new", help="Create a new workflow")
    new_parser.add_argument("name", type=str, help="Name of the new workflow")
    new_parser.add_argument(
        "-k",
        "--keyword",
        type=str,
        required=True,
        help="The keyword to trigger the workflow",
    )
    new_parser.add_argument(
        "-b",
        "--bundle-id",
        type=str,
        required=True,
        help="The bundle identifier, usually in reverse DNS notation",
    )
    new_parser.add_argument("--author", type=str, required=True, help="Name of the author")
    new_parser.add_argument("--website", type=str, help="The workflow website")
    new_parser.add_argument("--description", type=str, help="A description for the workflow")
    new_parser.add_argument(
        "--git",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Whether to create a git repository",
    )
    new_parser.add_argument(
        "--link", action=argparse.BooleanOptionalAction, default=True, help="Create a symbolic link to this workflow"
    )
    new_parser.add_argument(
        "--vendor", action=argparse.BooleanOptionalAction, default=True, help="Install workflow dependencies"
    )
    new_parser.set_defaults(func=new)

    vendor_parser = subparsers.add_parser("vendor", help="Install workflow dependencies")
    vendor_parser.add_argument(
        "--upgrade",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to pass `--upgrade` to `pip install` when vendoring",
    )
    vendor_parser.set_defaults(func=vendor)

    link_parser = subparsers.add_parser("link", help="Create a symbolic link to this workflow in Alfred")
    link_parser.add_argument(
        "--relink",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to delete (if exists) and recreate the link",
    )
    link_parser.add_argument(
        "--same-path",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to reuse (if exists) the previous path for the link",
    )
    link_parser.set_defaults(func=link)

    def version_str(arg_value: str, pat=re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")) -> str:
        if not pat.match(arg_value):
            raise argparse.ArgumentTypeError("invalid version string")
        return arg_value

    release_parser = subparsers.add_parser("release", help="Update version & tag for release build")
    release_parser.add_argument("--version", type=version_str, required=True, help="Version to update")
    release_parser.set_defaults(func=release)

    package_parser = subparsers.add_parser("package", help="Package the workflow for distribution")
    package_parser.add_argument("--name", type=str, required=True, help="The name of the workflow file")
    package_parser.set_defaults(func=package)

    version_parser = subparsers.add_parser("version", help="Display the version of the workflow")
    version_parser.set_defaults(func=version)

    name_parser = subparsers.add_parser("name", help="Display the name of the workflow")
    name_parser.set_defaults(func=name)

    show_link_parser = subparsers.add_parser(
        "show-link", help="Display the link in the installed workflows directory for this project"
    )
    show_link_parser.set_defaults(func=show_link)

    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=logging.DEBUG if args.debug else logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    args.func(args)


if __name__ == "__main__":
    _cli()
