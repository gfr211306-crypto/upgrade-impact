"""Day 8 checks using real wheels and the public CLI."""

import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize(
    "distribution,old,new,import_name",
    [
        ("python-dateutil", "2.8.2", "2.9.0.post0", "dateutil"),
        ("PyYAML", "6.0.1", "6.0.2", "yaml"),
        ("beautifulsoup4", "4.12.2", "4.12.3", "bs4"),
    ],
)
def test_detects_real_distribution_import_name(tmp_path, distribution, old, new, import_name):
    (tmp_path / "app.py").write_text(f"import {import_name}\n", encoding="utf-8")
    result = subprocess.run(
        ["upgrade-impact", distribution, old, new, str(tmp_path), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=300, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report == {
        "package": distribution,
        "old": old,
        "new": new,
        "findings": [],
        "summary": {"breaking": 0, "review": 0, "status": "ok"},
        "error": None,
    }


def test_real_breaking_incident_has_json_findings():
    fixture = Path(__file__).parent / "fixtures" / "demo"
    result = subprocess.run(
        ["upgrade-impact", "MarkupSafe", "2.0.1", "2.1.0", str(fixture), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=300, check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert {item["line"] for item in report["findings"]} == {1, 9}
    assert all(item["severity"] == "breaking" for item in report["findings"])
    assert report["summary"] == {"breaking": 2, "review": 0, "status": "ok"}
    assert report["error"] is None


def test_unused_real_distribution_reports_not_imported(tmp_path):
    (tmp_path / "app.py").write_text("import pathlib\n", encoding="utf-8")
    result = subprocess.run(
        ["upgrade-impact", "python-dateutil", "2.8.2", "2.9.0.post0", str(tmp_path), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=300, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["summary"] == {"breaking": 0, "review": 0, "status": "not imported"}
    assert report["findings"] == []
    assert report["error"] is None


def test_real_source_only_release_returns_json_tool_error(tmp_path):
    result = subprocess.run(
        ["upgrade-impact", "functools32", "3.2.3-2", "3.2.3-1", str(tmp_path), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=300, check=False,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["summary"]["status"] == "error"
    assert report["error"].startswith("No wheel is available for functools32==3.2.3-2;")
    assert "build code" in report["error"]
