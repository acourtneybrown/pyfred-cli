import json
from pathlib import PosixPath

import pytest

from pyfred.model import CacheConfig, Environment, OutputItem, ScriptFilterOutput
from pyfred.workflow import external_script, script_filter


@pytest.fixture(autouse=True)
def alfred_environment(monkeypatch):
    monkeypatch.setenv("alfred_preferences", "/Users/Crayons/Dropbox/Alfred/Alfred.alfredpreferences")
    monkeypatch.setenv("alfred_preferences", "/Users/Crayons/Dropbox/Alfred/Alfred.alfredpreferences")
    monkeypatch.setenv("alfred_preferences_localhash", "adbd4f66bc3ae8493832af61a41ee609b20d8705")
    monkeypatch.setenv("alfred_theme", "alfred.theme.yosemite")
    monkeypatch.setenv("alfred_theme_background", "rgba(255,255,255,0.98)")
    monkeypatch.setenv("alfred_theme_selection_background", "rgba(0,0,0,0.98)")
    monkeypatch.setenv("alfred_theme_subtext", "3")
    monkeypatch.setenv("alfred_version", "5.5")
    monkeypatch.setenv("alfred_version_build", "2058")
    monkeypatch.setenv("alfred_workflow_bundleid", "com.alfredapp.googlesuggest")
    monkeypatch.setenv(
        "alfred_workflow_cache",
        "/Users/Crayons/Library/Caches/com.runningwithcrayons.Alfred/Workflow Data/com.alfredapp.googlesuggest",
    )
    monkeypatch.setenv(
        "alfred_workflow_data",
        "/Users/Crayons/Library/Application Support/Alfred/Workflow Data/com.alfredapp.googlesuggest",
    )
    monkeypatch.setenv("alfred_workflow_name", "Google Suggest")
    monkeypatch.setenv("alfred_workflow_version", "1.7")
    monkeypatch.setenv("alfred_workflow_uid", "user.workflow.B0AC54EC-601C-479A-9428-01F9FD732959")
    monkeypatch.setenv("alfred_debug", "1")
    monkeypatch.setenv("alfred_workflow_description", "A workflow description")
    monkeypatch.setenv("alfred_workflow_keyword", "goog")


def _assert_env(env: Environment) -> None:
    assert env == Environment(
        debug=True,
        preferences_file=PosixPath("/Users/Crayons/Dropbox/Alfred/Alfred.alfredpreferences"),
        preferences_localhash="adbd4f66bc3ae8493832af61a41ee609b20d8705",
        version="5.5",
        version_build="2058",
        workflow_name="Google Suggest",
        workflow_version="1.7",
        workflow_bundleid="com.alfredapp.googlesuggest",
        workflow_uid="user.workflow.B0AC54EC-601C-479A-9428-01F9FD732959",
        workflow_cache=PosixPath(
            "/Users/Crayons/Library/Caches/com.runningwithcrayons.Alfred/Workflow Data/com.alfredapp.googlesuggest"
        ),
        workflow_data=PosixPath(
            "/Users/Crayons/Library/Application Support/Alfred/Workflow Data/com.alfredapp.googlesuggest"
        ),
        theme="alfred.theme.yosemite",
        theme_background="rgba(255,255,255,0.98)",
        theme_selection_background="rgba(0,0,0,0.98)",
        theme_subtext="3",
        workflow_description="A workflow description",
        workflow_keyword="goog",
    )


def test_decorator(capsys, monkeypatch):
    @script_filter
    def under_test(path, args, env):
        assert path.exists()
        assert isinstance(args, list)
        _assert_env(env)
        return ScriptFilterOutput(
            items=[OutputItem(title="Hello Alfred!")],
            rerun=2.0,
            cache=CacheConfig(seconds=10),
        )

    under_test()

    output = json.loads(capsys.readouterr().out)

    assert output == {
        "items": [{"title": "Hello Alfred!", "type": "default"}],
        "rerun": 2.0,
        "cache": {"seconds": 10},
    }


external_script_testdata = [
    (["abc", "def"], "abc def"),
    ("abc", "abc"),
]


@pytest.mark.parametrize("ret,expected", external_script_testdata)
def test_external_script_decorator(capsys, monkeypatch, ret, expected):
    @external_script
    def under_test(path, args, env):
        assert path.exists()
        assert isinstance(args, list)
        _assert_env(env)
        return ret

    under_test()

    output = capsys.readouterr().out

    assert output == expected
