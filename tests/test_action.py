"""The GitHub Action's analysis step, driven by the JSON inputs of a real job."""

import json
from pathlib import Path
import subprocess
from typing import Any
from unittest.mock import Mock

import pytest

from upgrade_impact import action
from upgrade_impact.action import ActionError, Upgrade, check, main, select_upgrades
from upgrade_impact.comment import MARKER, render


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
BLOB_URL = f"https://github.com/octo-org/octo-repo/blob/{COMMIT}"


def dependabot(name: str) -> str:
    return (FIXTURES / "dependabot" / f"{name}.json").read_text(encoding="utf-8")


def report(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "reports" / f"{name}.json").read_text(encoding="utf-8"))


def run_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **inputs: str
) -> tuple[int, dict[str, str]]:
    """Run the step like action.yml does, in a workspace with a src directory."""
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace)
    output = tmp_path / "github-output"
    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "octo-org/octo-repo",
        "GITHUB_OUTPUT": str(output),
        "RUNNER_TEMP": str(tmp_path / "runner-temp"),
        "HEAD_SHA": COMMIT,
        "INPUT_PATH": ".",
        "INPUT_FAIL_ON_BREAKING": "true",
        **inputs,
    }
    code = main(env)
    lines = output.read_text(encoding="utf-8").splitlines() if output.exists() else []
    return code, dict(line.split("=", 1) for line in lines)


def fake_check(reports: dict[str, dict[str, Any]]) -> Mock:
    return Mock(side_effect=lambda upgrade, path: reports[upgrade.package])


def test_selects_each_python_update_from_fetch_metadata_once() -> None:
    assert select_upgrades(dependabot("pip-group"), "", "", "") == [
        Upgrade("urllib3", "1.26.15", "2.0.0"),
        Upgrade("MarkupSafe", "2.0.1", "2.1.0"),
        Upgrade("Jinja2", "", "3.1.4"),
    ]


@pytest.mark.parametrize(
    ("ecosystem", "selected"),
    [("pip", True), ("uv", True), ("npm_and_yarn", False), ("github_actions", False), (None, False)],
)
def test_checks_only_pip_and_uv_updates(ecosystem: str | None, selected: bool) -> None:
    update = {
        "dependencyName": "urllib3", "prevVersion": "1.26.15", "newVersion": "2.0.0",
        "packageEcosystem": ecosystem,
    }

    upgrades = select_upgrades(json.dumps([update]), "", "", "")

    assert upgrades == ([Upgrade("urllib3", "1.26.15", "2.0.0")] if selected else [])


def test_manual_inputs_select_one_upgrade() -> None:
    assert select_upgrades("", "MarkupSafe", "2.0.1", "2.1.0") == [
        Upgrade("MarkupSafe", "2.0.1", "2.1.0"),
    ]


@pytest.mark.parametrize(
    ("dependencies", "manual", "message"),
    [
        ("", ("", "", ""), "nothing to check"),
        ("", ("MarkupSafe", "2.0.1", ""), "must be set together"),
        ("[]", ("MarkupSafe", "2.0.1", "2.1.0"), "not both"),
        ("{not json", ("", "", ""), "not valid JSON"),
        ('{"packageEcosystem": "pip"}', ("", "", ""), "JSON array of objects"),
        ('["pip"]', ("", "", ""), "JSON array of objects"),
    ],
)
def test_rejects_unusable_inputs(
    dependencies: str, manual: tuple[str, str, str], message: str
) -> None:
    with pytest.raises(ActionError, match=message):
        select_upgrades(dependencies, *manual)


def test_check_returns_the_clis_report_for_a_real_upgrade() -> None:
    assert check(Upgrade("MarkupSafe", "2.0.1", "2.1.0"), str(FIXTURES / "demo")) == report("markupsafe")


def test_check_passes_option_like_names_as_arguments(tmp_path: Path) -> None:
    result = check(Upgrade("--help", "1.0", "2.0"), str(tmp_path))

    assert result["package"] == "--help"
    assert result["summary"]["status"] == "error"
    assert result["error"] == "invalid distribution name: '--help'"


def test_check_needs_both_versions_before_running_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("the CLI must not run")))

    result = check(Upgrade("Jinja2", "", "3.1.4"), ".")

    assert result == {
        "package": "Jinja2",
        "old": "",
        "new": "3.1.4",
        "findings": [],
        "summary": {"breaking": 0, "review": 0, "status": "error"},
        "error": "the update has no old version",
    }


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (
            subprocess.CompletedProcess([], -9, stdout="", stderr="Killed\n"),
            "upgrade-impact exited with code -9 without a report: Killed",
        ),
        (
            subprocess.CompletedProcess([], 1, stdout='{"findings": "none"}', stderr=""),
            'upgrade-impact exited with code 1 without a report: {"findings": "none"}',
        ),
        (subprocess.TimeoutExpired([], 600), "timed out after 10 minutes"),
        (OSError("no such file"), "could not run upgrade-impact: no such file"),
    ],
)
def test_check_turns_any_failure_into_an_error_report(
    monkeypatch: pytest.MonkeyPatch, outcome: Any, reason: str
) -> None:
    run = Mock(side_effect=outcome) if isinstance(outcome, BaseException) else Mock(return_value=outcome)
    monkeypatch.setattr(subprocess, "run", run)

    result = check(Upgrade("urllib3", "1.26.15", "2.0.0"), ".")

    assert result["summary"]["status"] == "error"
    assert result["error"] == reason
    assert (result["package"], result["old"], result["new"]) == ("urllib3", "1.26.15", "2.0.0")


def test_writes_one_comment_for_all_dependabot_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reports = {
        "urllib3": report("urllib3"),
        "MarkupSafe": report("markupsafe"),
        "Jinja2": check(Upgrade("Jinja2", "", "3.1.4"), "."),
    }
    monkeypatch.setattr(action, "check", fake_check(reports))

    code, outputs = run_action(
        tmp_path, monkeypatch, INPUT_UPDATED_DEPENDENCIES_JSON=dependabot("pip-group")
    )

    assert code == 0
    assert outputs == {
        "comment-file": (tmp_path / "runner-temp" / "upgrade-impact" / "comment.md").as_posix(),
        "breaking": "4",
        "fail": "true",
    }
    body = Path(outputs["comment-file"]).read_text(encoding="utf-8")
    assert body == render(list(reports.values()), blob_url=BLOB_URL, root="")
    assert body.startswith(MARKER)
    assert f"]({BLOB_URL}/app.py#L6)" in body
    assert "could not analyze\\: the update has no old version" in body
    assert capsys.readouterr().out.splitlines() == [
        "upgrade-impact: urllib3 1.26.15 → 2.0.0: ❌ 2 breaking",
        "upgrade-impact: MarkupSafe 2.0.1 → 2.1.0: ❌ 2 breaking",
        "upgrade-impact: Jinja2 → 3.1.4: could not analyze: the update has no old version",
    ]


def test_one_failing_package_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "urllib3" in command:
            return subprocess.CompletedProcess(command, -11, stdout="", stderr="Segmentation fault")
        return subprocess.CompletedProcess(command, 1, stdout=json.dumps(report("markupsafe")), stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    code, outputs = run_action(
        tmp_path, monkeypatch, INPUT_UPDATED_DEPENDENCIES_JSON=dependabot("pip-group")
    )

    assert code == 0
    assert (outputs["breaking"], outputs["fail"]) == ("2", "true")
    rows = [
        line for line in Path(outputs["comment-file"]).read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
    ]
    assert rows == [
        "| `urllib3` | `1.26.15` → `2.0.0` | could not analyze\\: upgrade\\-impact exited with "
        "code \\-11 without a report\\: Segmentation fault |",
        "| `MarkupSafe` | `2.0.1` → `2.1.0` | ❌ 2 breaking |",
        "| `Jinja2` | `?` → `3.1.4` | could not analyze\\: the update has no old version |",
    ]


@pytest.mark.parametrize(
    ("value", "fail"),
    [("true", "true"), ("True", "true"), ("TRUE", "true"),
     ("false", "false"), ("False", "false"), ("FALSE", "false")],
)
def test_fail_on_breaking_decides_whether_the_job_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, fail: str
) -> None:
    monkeypatch.setattr(action, "check", fake_check({"MarkupSafe": report("markupsafe")}))

    code, outputs = run_action(
        tmp_path, monkeypatch, INPUT_PACKAGE="MarkupSafe", INPUT_OLD_VERSION="2.0.1",
        INPUT_NEW_VERSION="2.1.0", INPUT_FAIL_ON_BREAKING=value,
    )

    assert code == 0
    assert (outputs["breaking"], outputs["fail"]) == ("2", fail)


def test_review_findings_never_fail_the_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(action, "check", fake_check({"urllib3": report("review")}))

    code, outputs = run_action(
        tmp_path, monkeypatch, INPUT_PACKAGE="urllib3", INPUT_OLD_VERSION="1.26.15",
        INPUT_NEW_VERSION="2.0.0",
    )

    assert (code, outputs["breaking"], outputs["fail"]) == (0, "0", "false")


def test_updates_outside_pip_and_uv_leave_nothing_to_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(action, "check", Mock(side_effect=AssertionError("nothing to check")))

    code, outputs = run_action(
        tmp_path, monkeypatch, INPUT_UPDATED_DEPENDENCIES_JSON=dependabot("github-actions")
    )

    assert code == 0
    assert outputs == {"comment-file": "", "breaking": "0", "fail": "false"}
    assert not (tmp_path / "runner-temp" / "upgrade-impact").exists()
    assert capsys.readouterr().out == "upgrade-impact: no pip or uv updates to check\n"


@pytest.mark.parametrize(
    ("inputs", "error"),
    [
        (
            {"GITHUB_EVENT_NAME": "pull_request_target", "INPUT_UPDATED_DEPENDENCIES_JSON": "[]"},
            "upgrade-impact must not run on pull_request_target; trigger it with pull_request",
        ),
        (
            {"INPUT_FAIL_ON_BREAKING": "yes", "INPUT_UPDATED_DEPENDENCIES_JSON": "[]"},
            "fail-on-breaking must be true or false, not 'yes'",
        ),
        (
            {"INPUT_PATH": "missing\n::warning::injected", "INPUT_UPDATED_DEPENDENCIES_JSON": "[]"},
            "path is not a directory: missing%0A::warning::injected",
        ),
        ({}, "nothing to check: set updated-dependencies-json to the output of "
             "dependabot/fetch-metadata, or set package, old-version and new-version"),
    ],
)
def test_unusable_inputs_fail_the_step_before_checking_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    inputs: dict[str, str], error: str,
) -> None:
    monkeypatch.setattr(action, "check", Mock(side_effect=AssertionError("nothing to check")))

    code, outputs = run_action(tmp_path, monkeypatch, **inputs)

    assert (code, outputs) == (1, {})
    # One escaped line, so the message cannot inject another workflow command.
    assert capsys.readouterr().out == f"::error::{error}\n"


@pytest.mark.parametrize(
    ("path", "commit", "link"),
    [
        ("src", COMMIT, f"]({BLOB_URL}/src/app.py#L1)"),
        ("absolute src", COMMIT, f"]({BLOB_URL}/src/app.py#L1)"),
        (".", COMMIT, f"]({BLOB_URL}/app.py#L1)"),
        (".", "not-a-commit", None),
        ("outside", COMMIT, None),
    ],
)
def test_findings_link_to_the_checked_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, commit: str, link: str | None
) -> None:
    monkeypatch.setattr(action, "check", fake_check({"MarkupSafe": report("markupsafe")}))
    if path == "absolute src":
        path = str(tmp_path / "workspace" / "src")
    elif path == "outside":
        path = str(tmp_path / "outside")
        Path(path).mkdir()

    code, outputs = run_action(
        tmp_path, monkeypatch, INPUT_PACKAGE="MarkupSafe", INPUT_OLD_VERSION="2.0.1",
        INPUT_NEW_VERSION="2.1.0", INPUT_PATH=path, HEAD_SHA=commit,
    )

    body = Path(outputs["comment-file"]).read_text(encoding="utf-8")
    if link is None:
        assert "](" not in body
    else:
        assert link in body


def test_manual_inputs_check_a_real_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github-output"
    monkeypatch.chdir(PROJECT_ROOT)

    code = main({
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKSPACE": str(PROJECT_ROOT),
        "GITHUB_REPOSITORY": "octo-org/octo-repo",
        "GITHUB_OUTPUT": str(output),
        "RUNNER_TEMP": str(tmp_path),
        "HEAD_SHA": COMMIT,
        "INPUT_PACKAGE": "MarkupSafe",
        "INPUT_OLD_VERSION": "2.0.1",
        "INPUT_NEW_VERSION": "2.1.0",
        "INPUT_PATH": "tests/fixtures/demo",
    })

    assert code == 0
    assert output.read_text(encoding="utf-8").splitlines() == [
        f"comment-file={(tmp_path / 'upgrade-impact' / 'comment.md').as_posix()}",
        "breaking=2",
        "fail=true",
    ]
    body = (tmp_path / "upgrade-impact" / "comment.md").read_text(encoding="utf-8")
    assert body == render([report("markupsafe")], blob_url=BLOB_URL, root="tests/fixtures/demo")
