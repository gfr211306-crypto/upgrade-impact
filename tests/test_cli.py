import io
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from upgrade_impact import cli
from upgrade_impact.check import CheckError, Finding


@pytest.mark.parametrize("import_name", [None, "yaml"])
def test_forwards_arguments_and_reports_no_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    import_name: str | None,
) -> None:
    checker = Mock(return_value=[])
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
        Mock(return_value=[
            Finding(Path("app.py"), 1, "markupsafe.soft_unicode", "soft_unicode was removed"),
            Finding(Path("app.py"), 9, "markupsafe.soft_unicode", "soft_unicode was removed"),
        ]),
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
        Mock(return_value=[
            Finding(Path("app.py"), 6, "urllib3.Retry", "review changed default", "review"),
        ]),
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
    out = capsys.readouterr().out
    for text in ("distribution", "old_version", "new_version", "repo_path", "--import-name",
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
    checker = Mock(return_value=[])
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
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp950")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp950")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(cli, "check", Mock(return_value=[
        Finding(Path("app.py"), 1, "pkg.f", "pkg.f was removed"),
        Finding(Path("app.py"), 2, "pkg.g", "review pkg.g", "review"),
    ]))

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
