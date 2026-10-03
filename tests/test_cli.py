import io
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from upgrade_impact import cli
from upgrade_impact.check import CheckError, CheckResult, Finding


@pytest.mark.parametrize("import_name", [None, "yaml"])
def test_forwards_arguments_and_reports_no_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    import_name: str | None,
) -> None:
    checker = Mock(return_value=CheckResult([]))
    monkeypatch.setattr(cli, "check", checker)
    arguments = ["PyYAML", "5.4.1", "6.0", str(tmp_path)]
    if import_name is not None:
        arguments.extend(["--import-name", import_name])

    assert cli.main(arguments) == 0

    checker.assert_called_once()
    assert checker.call_args.args[:3] == ("PyYAML", "5.4.1", "6.0")
    assert len(checker.call_args.args) == 4
    assert Path(checker.call_args.args[3]) == tmp_path
    assert checker.call_args.kwargs == {"import_name": import_name}
    captured = capsys.readouterr()
    assert captured.out == "0 breaking · 0 to review\n"
    assert captured.err == ""


def test_reports_breaking_findings_and_returns_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "check",
        Mock(return_value=CheckResult([
            Finding(Path("app.py"), 1, "markupsafe.soft_unicode", "soft_unicode was removed"),
            Finding(Path("app.py"), 9, "markupsafe.soft_unicode", "soft_unicode was removed"),
        ])),
    )

    assert cli.main(["MarkupSafe", "2.0.1", "2.1.0", str(tmp_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == (
        "❌ app.py:1  soft_unicode was removed\n"
        "❌ app.py:9  soft_unicode was removed\n"
        "2 breaking · 0 to review\n"
    )
    assert captured.err == ""


def test_review_findings_do_not_set_breaking_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "check",
        Mock(return_value=CheckResult([
            Finding(Path("app.py"), 6, "urllib3.Retry", "review changed default", "review"),
        ])),
    )

    assert cli.main(["urllib3", "1.26.15", "2.0.0", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert captured.out == (
        "⚠️ app.py:6  review changed default\n"
        "0 breaking · 1 to review\n"
    )
    assert captured.err == ""


@pytest.mark.parametrize("error", [CheckError("cannot load package"), OSError("cannot read repo")])
def test_tool_errors_go_to_stderr_without_success_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    monkeypatch.setattr(cli, "check", Mock(side_effect=error))

    assert cli.main(["MarkupSafe", "2.0.1", "2.1.0", str(tmp_path)]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {error}\n"


def test_missing_arguments_exit_two_without_checking_packages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    checker = Mock()
    monkeypatch.setattr(cli, "check", checker)

    with pytest.raises(SystemExit) as error:
        cli.main([])

    assert error.value.code == 2
    checker.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err
    assert "required" in captured.err


def test_missing_repository_errors_before_loading_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    loader = Mock()
    monkeypatch.setattr("griffe.load_pypi", loader)
    missing_repo = tmp_path / "missing-repository"

    assert cli.main(["MarkupSafe", "2.0.1", "2.1.0", str(missing_repo)]) == 2

    loader.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert str(missing_repo) in captured.err


def test_help_explains_arguments_output_and_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["--help"])

    assert error.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    for text in ("distribution", "old_version", "new_version", "repo_path", "--import-name",
                 "--format", "auto-detect", "wheel metadata",
                 "upgrade-impact urllib3 1.26.15 2.0.0", "exit codes", "tool error"):
        assert text in out


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["bad name", "1.0", "2.0"], "invalid distribution name: 'bad name'"),
        (["pkg-", "1.0", "2.0"], "invalid distribution name: 'pkg-'"),
        (["pkg", ">=1.0", "2.0"], "invalid old version '>=1.0'"),
        (["pkg", "1.0", "==2.0"], "invalid new version '==2.0'"),
        (["pkg", "1.0", "2.0 "], "invalid new version '2.0 '"),
        (["pkg", "2.0", "2.0"], "old and new versions are both 2.0"),
        (["pkg", "1.0", "2.0", "--import-name", "my-pkg"], "invalid import name: 'my-pkg'"),
        (["pkg", "1.0", "2.0", "--import-name", "pkg..sub"], "invalid import name: 'pkg..sub'"),
    ],
)
def test_invalid_arguments_exit_two_before_checking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    arguments: list[str], message: str,
) -> None:
    checker = Mock()
    monkeypatch.setattr(cli, "check", checker)
    arguments = [*arguments[:3], str(tmp_path), *arguments[3:]]

    assert cli.main(arguments) == 2

    checker.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: {message}")


def test_repository_path_must_be_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    checker = Mock()
    monkeypatch.setattr(cli, "check", checker)
    file = tmp_path / "app.py"
    file.write_text("", encoding="utf-8")

    assert cli.main(["pkg", "1.0", "2.0", str(file)]) == 2

    checker.assert_not_called()
    assert capsys.readouterr().err == f"error: repository path is not a directory: {file}\n"


@pytest.mark.parametrize(
    ("distribution", "old_version", "new_version"),
    [("zope.interface", "5.0", "6.0"), ("pkg", "2.0.0rc1", "1!2.0.post1+local.7"), ("A", "1", "2")],
)
def test_accepts_valid_names_and_exact_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    distribution: str, old_version: str, new_version: str,
) -> None:
    checker = Mock(return_value=CheckResult([]))
    monkeypatch.setattr(cli, "check", checker)

    assert cli.main([distribution, old_version, new_version, str(tmp_path)]) == 0
    checker.assert_called_once()


def test_unexpected_errors_exit_two_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "check", Mock(side_effect=KeyError("boom")))

    assert cli.main(["pkg", "1.0", "2.0", str(tmp_path)]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: unexpected KeyError: 'boom'\n"


def test_output_is_utf8_on_a_legacy_windows_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp950", newline="\n")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp950", newline="\n")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(cli, "check", Mock(return_value=CheckResult([
        Finding(Path("app.py"), 1, "pkg.f", "pkg.f was removed"),
        Finding(Path("app.py"), 2, "pkg.g", "review pkg.g", "review"),
    ])))

    assert cli.main(["pkg", "1.0", "2.0", str(tmp_path)]) == 1

    stdout.flush()
    assert stdout.buffer.getvalue().decode("utf-8") == (
        "❌ app.py:1  pkg.f was removed\n"
        "⚠️ app.py:2  review pkg.g\n"
        "1 breaking · 1 to review\n"
    )

    monkeypatch.setattr(cli, "check", Mock(side_effect=CheckError("cannot load 套件")))
    assert cli.main(["pkg", "1.0", "2.0", str(tmp_path)]) == 2
    stderr.flush()
    assert stderr.buffer.getvalue().decode("utf-8") == "error: cannot load 套件\n"


@pytest.mark.parametrize(
    ("findings", "exit_code", "breaking", "review"),
    [
        ([], 0, 0, 0),
        ([Finding(Path("nested/app.py"), 9, "pkg.f", "pkg.f was removed")], 1, 1, 0),
        ([Finding(Path("app.py"), 6, "pkg.f", "review changed default", "review")], 0, 0, 1),
        ([
            Finding(Path("app.py"), 9, "pkg.f", "pkg.f was removed"),
            Finding(Path("app.py"), 6, "pkg.g", "review changed default", "review"),
        ], 1, 1, 1),
    ],
)
def test_json_findings_and_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    findings: list[Finding], exit_code: int, breaking: int, review: int,
) -> None:
    monkeypatch.setattr(cli, "check", Mock(return_value=CheckResult(findings)))

    assert cli.main(["pkg", "1.0", "2.0", str(tmp_path), "--format", "json"]) == exit_code

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "package": "pkg",
        "old": "1.0",
        "new": "2.0",
        "findings": [
            {"file": finding.file.as_posix(), "line": finding.line,
             "severity": finding.severity, "message": finding.message}
            for finding in findings
        ],
        "summary": {"breaking": breaking, "review": review, "status": "ok"},
        "error": None,
    }
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    assert "❌" not in captured.out
    assert "⚠" not in captured.out


def test_abbreviated_format_option_selects_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "check", Mock(return_value=CheckResult([])))

    assert cli.main(["pkg", "1.0", "2.0", str(tmp_path), "--for", "json"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["package"] == "pkg"
    assert payload["summary"] == {"breaking": 0, "review": 0, "status": "ok"}
    assert payload["error"] is None
    assert captured.err == ""


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_unused_distribution_reports_not_imported_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    output_format: str,
) -> None:
    monkeypatch.setattr(cli, "check", Mock(return_value=CheckResult([], imported=False)))

    assert cli.main(["pkg", "1.0", "2.0", str(tmp_path), "--format", output_format]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    if output_format == "text":
        assert captured.out == "not imported\n0 breaking · 0 to review\n"
    else:
        payload = json.loads(captured.out)
        assert payload["findings"] == []
        assert payload["summary"] == {"breaking": 0, "review": 0, "status": "not imported"}
        assert payload["error"] is None


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (CheckError("no wheel is available"), "no wheel is available"),
        (OSError("cannot read repo"), "cannot read repo"),
        (KeyError("boom"), "unexpected KeyError: 'boom'"),
    ],
)
def test_json_tool_errors_produce_one_object_without_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    error: Exception, message: str,
) -> None:
    monkeypatch.setattr(cli, "check", Mock(side_effect=error))

    assert cli.main(["pkg", "1.0", "2.0", str(tmp_path), "--format=json"]) == 2

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "package": "pkg", "old": "1.0", "new": "2.0", "findings": [],
        "summary": {"breaking": 0, "review": 0, "status": "error"}, "error": message,
    }
    assert captured.err == ""


@pytest.mark.parametrize(
    ("arguments", "error_part", "package", "old", "new"),
    [
        (["--format", "json"], "required", None, None, None),
        (["pkg", "1.0", "--format=json"], "required", "pkg", "1.0", None),
        (["pkg", "1.0", "2.0", ".", "--format=json", "--unknown"],
         "unrecognized arguments", "pkg", "1.0", "2.0"),
        (["pkg", "1.0", "2.0", ".", "--format=json", "--import-name"],
         "expected one argument", "pkg", "1.0", "2.0"),
    ],
)
def test_json_argument_parser_errors_use_the_same_schema(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    arguments: list[str], error_part: str,
    package: str | None, old: str | None, new: str | None,
) -> None:
    checker = Mock()
    monkeypatch.setattr(cli, "check", checker)

    assert cli.main(arguments) == 2

    checker.assert_not_called()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert set(payload) == {"package", "old", "new", "findings", "summary", "error"}
    assert (payload["package"], payload["old"], payload["new"]) == (package, old, new)
    assert payload["findings"] == []
    assert payload["summary"] == {"breaking": 0, "review": 0, "status": "error"}
    assert error_part in payload["error"]
    assert captured.err == ""


def test_json_validation_errors_before_checking_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    checker = Mock()
    monkeypatch.setattr(cli, "check", checker)

    assert cli.main(["bad name", "1.0", "2.0", str(tmp_path), "--format", "json"]) == 2

    checker.assert_not_called()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["package"] == "bad name"
    assert payload["error"] == "invalid distribution name: 'bad name'"
    assert payload["summary"]["status"] == "error"
    assert captured.err == ""


def test_json_utf8_paths_messages_and_errors_on_legacy_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp950", newline="\n")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp950", newline="\n")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(cli, "check", Mock(return_value=CheckResult([
        Finding(Path("套件/使用.py"), 3, "pkg.f", "pkg.f removed: café"),
    ])))
    arguments = ["pkg", "1.0", "2.0", str(tmp_path), "--format", "json"]

    assert cli.main(arguments) == 1

    stdout.flush()
    payload = json.loads(stdout.buffer.getvalue().decode("utf-8"))
    assert payload["findings"][0]["file"] == "套件/使用.py"
    assert payload["findings"][0]["message"] == "pkg.f removed: café"

    stdout.seek(0)
    stdout.truncate(0)
    monkeypatch.setattr(cli, "check", Mock(side_effect=CheckError("cannot load 套件")))
    assert cli.main(arguments) == 2
    stdout.flush()
    payload = json.loads(stdout.buffer.getvalue().decode("utf-8"))
    assert payload["error"] == "cannot load 套件"
    stderr.flush()
    assert stderr.buffer.getvalue() == b""
