"""Real upgrade incidents exercised through the public CLI, without mocks."""

from pathlib import Path
import re
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_REPO = PROJECT_ROOT / "tests" / "fixtures" / "demo"


@pytest.mark.parametrize(
    "distribution,old_version,new_version,expected_lines,removed_name,exit_code",
    [
        pytest.param(
            "MarkupSafe", "2.0.1", "2.1.0", {1, 9}, "soft_unicode", 1,
            id="markupsafe-soft-unicode-removal",
        ),
        pytest.param(
            "Jinja2", "3.0.3", "3.1.0", {2, 10}, "Markup", 1,
            id="jinja2-markup-removal",
        ),
        pytest.param(
            "urllib3", "1.26.15", "2.0.0", {6, 7}, "method_whitelist", 1,
            id="urllib3-method-whitelist-removal",
        ),
        pytest.param(
            "Jinja2", "3.1.0", "3.1.4", set(), None, 0,
            id="jinja2-compatible-patch",
        ),
    ],
)
def test_upgrade_acceptance(
    distribution, old_version, new_version, expected_lines, removed_name, exit_code
):
    result = subprocess.run(
        [
            "upgrade-impact",
            distribution,
            old_version,
            new_version,
            str(DEMO_REPO),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    diagnostics = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.returncode == exit_code, diagnostics

    output_lines = result.stdout.strip().splitlines()
    assert output_lines, diagnostics
    assert output_lines[-1] == (
        f"{len(expected_lines)} breaking · 0 to review"
    ), diagnostics
    findings = output_lines[:-1]
    assert len(findings) == len(expected_lines), diagnostics

    actual_lines = set()
    for finding in findings:
        match = re.fullmatch(
            r"❌\s+(?P<file>.+\.py):(?P<line>\d+)\s+(?P<message>.+)", finding
        )
        assert match is not None, diagnostics
        assert Path(match["file"]).name == "app.py", diagnostics
        assert removed_name in match["message"], diagnostics
        assert "removed" in match["message"].lower(), diagnostics
        actual_lines.add(int(match["line"]))

    assert actual_lines == expected_lines, diagnostics
