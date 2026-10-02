from pathlib import Path
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
