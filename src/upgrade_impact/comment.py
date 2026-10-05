"""Render upgrade-impact JSON reports as Markdown for a pull request comment.

Reports carry text from outside this repository: Dependabot metadata, error
messages, and names or default values taken from the packages themselves. All
of it is escaped, so it renders literally and cannot add links, images, HTML,
@mentions or #references to the comment.
"""

from collections.abc import Iterator, Mapping, Sequence
import posixpath
import re
from typing import Any
from urllib.parse import quote


# The hidden marker that identifies this tool's comment, so later runs update it.
MARKER = "<!-- upgrade-impact -->"
# GitHub rejects comment bodies over 65,536 characters; stay well below that in bytes.
MAX_BYTES = 60_000
# The longest error reason shown in the results table.
MAX_REASON = 300

_PUNCTUATION = re.compile(r"[!-/:-@\[-`{-~]")
_WHITESPACE = re.compile(r"\s+")
_BACKTICKS = re.compile(r"`+")
_ICONS = {"breaking": "❌", "review": "⚠️"}

Report = Mapping[str, Any]


def _text(value: str) -> str:
    """Escape text so that any Markdown or HTML in it renders literally."""
    text = _PUNCTUATION.sub(lambda match: "\\" + match.group(), _WHITESPACE.sub(" ", value))
    # Backslash escapes do not stop GitHub from linking @mentions and #references.
    return text.replace("@", "@\u200b").replace("#", "#\u200b")


def _code(value: str, *, table: bool = False) -> str:
    """Render text as a code span, inside which GitHub links nothing."""
    text = _WHITESPACE.sub(" ", value).strip()
    if not text:
        return ""
    fence = "`" * (max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
    if text.startswith("`") or text.endswith("`"):
        text = f" {text} "
    code = f"{fence}{text}{fence}"
    # A table cell ends at an unescaped pipe, even inside a code span.
    return code.replace("|", "\\|") if table else code


def _message(message: str) -> str:
    """Keep the `code` spans of a finding message and escape the rest."""
    text = _WHITESPACE.sub(" ", message).strip()
    parts = text.split("`")
    if len(parts) % 2 == 0:  # An unmatched backtick: show the message as text.
        return _text(text)
    return "".join(_code(part) if index % 2 else _text(part) for index, part in enumerate(parts))


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _field(report: Report, key: str) -> str:
    return str(report.get(key) or "?")


def _upgrade(report: Report, *, table: bool = False) -> str:
    return f"{_code(_field(report, 'old'), table=table)} → {_code(_field(report, 'new'), table=table)}"


def describe(report: Report) -> str:
    """Summarize one report as a single line of plain text."""
    summary = report["summary"]
    if report["error"] is not None or summary["status"] == "error":
        reason = _WHITESPACE.sub(" ", report["error"] or "unknown error").strip()
        return f"could not analyze: {reason}"
    if summary["status"] == "not imported":
        return "not imported"
    breaking, review = summary["breaking"], summary["review"]
    if breaking:
        return f"❌ {breaking} breaking" + (f" · {review} to review" if review else "")
    if review:
        return f"⚠️ {review} to review"
    return "✅ no findings"


def _repository_path(root: str | None, file: str) -> str | None:
    """Return a finding's path from the repository root, or None if it is outside."""
    if root is None:
        return None
    path = posixpath.normpath(posixpath.join(root, file))
    if path in (".", "..") or path.startswith(("/", "../")):
        return None
    return path


def _location(finding: Mapping[str, Any], blob_url: str | None, root: str | None) -> str:
    """Render ``file:line``, linked to that line of the commit when it is known."""
    path = _repository_path(root, finding["file"])
    label = _code(f"{path or finding['file']}:{finding['line']}")
    if blob_url is None or path is None:
        return label
    return f"[{label}]({blob_url}/{quote(path)}#L{finding['line']})"


def _finding_entries(
    reports: Sequence[Report], blob_url: str | None, root: str | None
) -> Iterator[list[str]]:
    """Yield the lines of each finding, preceded by its package's heading."""
    for report in reports:
        heading = ["", f"**{_code(_field(report, 'package'))}** {_upgrade(report)}", ""]
        for finding in report["findings"]:
            icon = _ICONS[finding["severity"]]
            location = _location(finding, blob_url, root)
            yield [*heading, f"- {icon} {location} {_message(finding['message'])}"]
            heading = []


def render(
    reports: Sequence[Report], *, blob_url: str | None = None, root: str | None = ""
) -> str:
    """Render a table of results, then every finding in a collapsed list.

    ``blob_url`` is the URL prefix of the commit's files, such as
    ``https://github.com/OWNER/REPO/blob/SHA``; when it is set, each finding
    links to its line. ``root`` is the scanned directory relative to the
    repository root: ``""`` for the root itself, None if it is outside.
    """
    lines = [
        MARKER,
        "### upgrade-impact",
        "",
        "| Package | Upgrade | Result |",
        "| --- | --- | --- |",
    ]
    for report in reports:
        package = _code(_field(report, "package"), table=True)
        result = _text(_shorten(describe(report), MAX_REASON))
        lines.append(f"| {package} | {_upgrade(report, table=True)} | {result} |")

    total = sum(len(report["findings"]) for report in reports)
    if total:
        lines += ["", "<details>", f"<summary>{_plural(total, 'finding')}</summary>"]
        # Leave room for the note about omitted findings and the closing tag.
        budget = MAX_BYTES - 200
        size = len("\n".join(lines).encode())
        shown = 0
        for entry in _finding_entries(reports, blob_url, root):
            cost = len("\n".join(entry).encode()) + 1
            if size + cost > budget:
                break
            lines += entry
            size += cost
            shown += 1
        if shown < total:
            lines += [
                "",
                f"{_plural(total - shown, 'more finding')} not shown. "
                "Run upgrade-impact locally to see all of them.",
            ]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"
