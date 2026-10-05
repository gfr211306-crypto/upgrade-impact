"""Render pull request comments from JSON reports produced by ``--format json``."""

import json
from pathlib import Path
import re
from typing import Any

import pytest

from upgrade_impact.comment import MARKER, MAX_BYTES, describe, render


FIXTURES = Path(__file__).parent / "fixtures"
BLOB_URL = "https://github.com/octo-org/octo-repo/blob/0123456789abcdef0123456789abcdef01234567"


def report(name: str) -> Any:
    return json.loads((FIXTURES / "reports" / f"{name}.json").read_text(encoding="utf-8"))


def expected(name: str) -> str:
    return (FIXTURES / "comments" / f"{name}.md").read_text(encoding="utf-8")


def outside_code_spans(markdown: str) -> str:
    """Drop code spans and keep backslash escapes, as CommonMark parses them."""
    kept = []
    index = 0
    while index < len(markdown):
        if markdown[index] == "\\":
            kept.append(markdown[index:index + 2])
            index += 2
        elif markdown[index] == "`":
            run = re.match(r"`+", markdown[index:]).group()
            close = re.compile(f"(?<!`){run}(?!`)").search(markdown, index + len(run))
            kept.append("" if close else run)
            index = close.end() if close else index + len(run)
        else:
            kept.append(markdown[index])
            index += 1
    return "".join(kept)


def test_renders_a_table_and_linked_findings_for_each_package() -> None:
    reports = [report(name) for name in ("markupsafe", "urllib3", "jinja2", "dateutil", "functools32")]

    body = render(reports, blob_url=BLOB_URL, root="tests/fixtures/demo")

    assert body == expected("group")
    assert body.startswith(MARKER + "\n")
    assert f"]({BLOB_URL}/tests/fixtures/demo/app.py#L6)" in body


def test_review_findings_keep_the_messages_code_spans() -> None:
    body = render([report("review")], blob_url=BLOB_URL, root="")

    assert body == expected("review")
    assert "changed from `_Default` to `DEFAULT_ALLOWED_METHODS`" in body


def test_untrusted_text_cannot_add_markup_links_or_mentions() -> None:
    body = render(report("hostile"), blob_url=BLOB_URL, root="")

    assert body == expected("hostile")
    assert body.count(MARKER) == 1
    rows = [line for line in body.splitlines() if line.startswith("| ")]
    # Escaped pipes keep every row at three cells.
    assert all(len(re.findall(r"(?<!\\)\|", row)) == 4 for row in rows)
    # Code spans render literally; outside them, only our own markup is unescaped.
    text = outside_code_spans(body)
    assert re.findall(r"(?<!\\)<(?!/?details>|/?summary>|!-- upgrade-impact -->)", text) == []
    links = re.findall(r"(?<!\\)\]\((.*?)\)", text)
    assert len(links) == 2
    assert all(link.startswith(BLOB_URL + "/") for link in links)
    # A zero-width space after @ and # keeps mentions and references unlinked.
    prose = re.sub(r"\]\([^)]*\)", "]", text).replace("### upgrade-impact", "")
    assert re.findall("[@#](?!\N{ZERO WIDTH SPACE})", prose) == []


@pytest.mark.parametrize(
    ("blob_url", "root", "file", "linked"),
    [
        (BLOB_URL, "", "app.py", "app.py"),
        (BLOB_URL, "sub/dir", "app.py", "sub/dir/app.py"),
        (BLOB_URL, "sub", "../app.py", "app.py"),
        (BLOB_URL, "", "a b#1.py", "a%20b%231.py"),
        (None, "", "app.py", None),
        (BLOB_URL, None, "app.py", None),
        (BLOB_URL, "", "../outside.py", None),
    ],
)
def test_links_findings_only_to_files_inside_the_repository(
    blob_url: str | None, root: str | None, file: str, linked: str | None
) -> None:
    data = report("markupsafe")
    data["findings"] = [{"file": file, "line": 9, "severity": "breaking", "message": "removed"}]

    body = render([data], blob_url=blob_url, root=root)

    entry = next(line for line in body.splitlines() if line.startswith("- ❌ "))
    if linked is None:
        assert "](" not in entry
        assert f"`{file}:9`" in entry
    else:
        assert entry.endswith(f"]({BLOB_URL}/{linked}#L9) removed")


def test_omits_the_details_when_there_are_no_findings() -> None:
    body = render([report("jinja2"), report("dateutil")])

    assert "<details>" not in body
    assert body.endswith("| `python-dateutil` | `2.8.2` → `2.9.0.post0` | not imported |\n")


def test_long_comments_stay_under_githubs_limit() -> None:
    data = report("urllib3")
    template = data["findings"][0]
    data["findings"] = [{**template, "line": line} for line in range(1, 5001)]
    data["summary"]["breaking"] = 5000

    body = render([data, report("markupsafe")], blob_url=BLOB_URL, root="")

    assert len(body.encode()) <= MAX_BYTES
    assert "<summary>5002 findings</summary>" in body
    shown = body.count("\n- ❌ ")
    assert 0 < shown < 5002
    assert f"\n{5002 - shown} more findings not shown. Run upgrade-impact locally" in body
    assert body.endswith("\n\n</details>\n")


@pytest.mark.parametrize(
    ("name", "summary"),
    [
        ("markupsafe", "❌ 2 breaking"),
        ("review", "⚠️ 1 to review"),
        ("jinja2", "✅ no findings"),
        ("dateutil", "not imported"),
        (
            "functools32",
            "could not analyze: No wheel is available for functools32==3.2.3-2; "
            "source distributions are disabled to avoid running build code",
        ),
    ],
)
def test_describes_each_report_on_one_line(name: str, summary: str) -> None:
    assert describe(report(name)) == summary


def test_describes_breaking_and_review_counts_together() -> None:
    data = report("markupsafe")
    data["summary"]["review"] = 3

    assert describe(data) == "❌ 2 breaking · 3 to review"
