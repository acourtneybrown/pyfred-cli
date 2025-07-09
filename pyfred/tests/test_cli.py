import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from pyfred.cli import (
    _get_workflows_directory,
    link,
    new,
    package,
    show_link,
    vendor,
)
from pyfred.model import Data, Icon, Key, OutputItem, ScriptFilterOutput, Text, Type


def test_new(tmpdir):
    tmpdir = Path(tmpdir)
    sync_dir = tmpdir / "sync"
    workflows = sync_dir / "Alfred.alfredpreferences/workflows"
    workflows.mkdir(parents=True)

    args = MagicMock(
        sprc=argparse.Namespace,
        keyword="test",
        bundle_id="com.example.test",
        author=None,
        website=None,
        description=None,
    )
    args.name = "test_wf"

    expected_git_call = call(["git", "init", "test_wf"])
    expected_vendor_call = call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            f"{tmpdir/'test_wf'/'requirements.txt'}",
            f"--target={tmpdir/'test_wf'/'Workflow'/'vendored'}",
        ]
    )

    with patch("pathlib.Path.cwd", return_value=tmpdir):
        with patch("pyfred.cli._get_sync_directory", return_value=sync_dir):
            with patch("subprocess.call", return_value=0) as mock_sub:
                new(args)
                assert mock_sub.call_count == 2
                mock_sub.assert_has_calls([expected_git_call, expected_vendor_call])

    assert (tmpdir / "test_wf/Workflow/workflow.py").exists()
    installed_workflows = list(workflows.iterdir())
    assert len(installed_workflows) == 1
    assert installed_workflows[0].is_symlink()
    assert installed_workflows[0].readlink() == tmpdir / "test_wf" / "Workflow"


def test_get_workflows_directory():
    expected = Path.home() / "Library/Application Support/Alfred/Alfred.alfredpreferences/workflows"

    with patch("pyfred.cli._get_sync_directory", return_value=None):
        assert _get_workflows_directory() == expected


def test_full_model_serialises_to_json():
    output = ScriptFilterOutput(
        rerun=4.2,
        items=[
            OutputItem(
                title="Hello Alfred!",
                subtitle="a string",
                uid="fake_uid",
                icon=Icon.uti("public.jpeg"),
                valid=True,
                match="Hi",
                autocomplete="Hello Alfred!",
                mods={
                    Key.Cmd: Data(
                        subtitle="My new subtitle",
                        arg="An overridden argument",
                        icon=Icon.file_icon("/System/Applications/Calendar.app"),
                        valid=True,
                    )
                },
                text=Text(large_type="Large type this", copy="Copy that"),
                quicklookurl="https://example.com",
                type=Type.Default,
            ),
            OutputItem(title="A minimal item"),
        ],
        variables={"key": 42},
    )

    assert json.dumps(output, default=vars)


def test_show_link_with_link(tmpdir):
    tmpdir = Path(tmpdir)
    wf_dir = tmpdir / "Workflow"
    wf_dir.mkdir(parents=True)
    link_path = Path("/path/to/workflow/link")

    with patch("pathlib.Path.cwd", return_value=tmpdir):
        with patch("pyfred.cli._info_plist_path") as mock_info_plist_path:
            mock_info_plist_path.return_value.exists.return_value = True
            with patch("pyfred.cli.find_workflow_link", return_value=link_path) as mock_find:
                with patch("builtins.print") as mock_print:
                    show_link(MagicMock(spec=argparse.Namespace))
                    mock_find.assert_called_once_with(wf_dir)
                    mock_print.assert_called_once_with(link_path)


def test_show_link_without_link(tmpdir):
    tmpdir = Path(tmpdir)
    wf_dir = tmpdir / "Workflow"
    wf_dir.mkdir(parents=True)

    with patch("pathlib.Path.cwd", return_value=tmpdir):
        with patch("pyfred.cli._info_plist_path") as mock_info_plist_path:
            mock_info_plist_path.return_value.exists.return_value = True
            with patch("pyfred.cli.find_workflow_link", return_value=None) as mock_find:
                with patch("logging.error") as mock_error:
                    with pytest.raises(SystemExit) as excinfo:
                        show_link(MagicMock(spec=argparse.Namespace))
                    assert excinfo.value.code == 1
                    mock_find.assert_called_once_with(wf_dir)
                    mock_error.assert_called_once_with("No workflow link found. Use 'pyfred link' to create one.")


def test_exits_if_not_in_workflow_dir(tmpdir):
    with patch("pathlib.Path.cwd", return_value=tmpdir):
        for func in (link, package, vendor, show_link):
            with pytest.raises(SystemExit) as excinfo:
                func(MagicMock(spec=argparse.Namespace))
            assert excinfo.value.code == 1
