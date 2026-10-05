"""The GitHub Action's analysis step: check each upgrade and render the comment.

Security: this step never receives a GitHub token. It reads its inputs from
environment variables set by action.yml, refuses to run on
pull_request_target, and writes the comment body to a file that only the
comment step sends to GitHub. Each package runs the CLI in its own process,
so one failing package cannot stop the others.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from upgrade_impact.comment import describe, render


# Dependabot ecosystems whose packages come from PyPI.
ECOSYSTEMS = frozenset({"pip", "uv"})
TIMEOUT_SECONDS = 600

_SEVERITIES = frozenset({"breaking", "review"})
_STATUSES = frozenset({"ok", "not imported", "error"})
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class ActionError(ValueError):
    """The action's inputs cannot be used."""


@dataclass(frozen=True)
class Upgrade:
    """One package upgrade to check."""

    package: str
    old: str
    new: str


def select_upgrades(dependencies_json: str, package: str, old: str, new: str) -> list[Upgrade]:
    """Return the upgrades to check, from manual inputs or Dependabot metadata.

    ``dependencies_json`` is the ``updated-dependencies-json`` output of
    dependabot/fetch-metadata. Updates outside the pip and uv ecosystems are
    skipped, and so are repeated updates of the same package and versions.
    """
    if package or old or new:
        if dependencies_json.strip():
            raise ActionError(
                "set either updated-dependencies-json, "
                "or package, old-version and new-version, not both"
            )
        if not (package and old and new):
            raise ActionError("package, old-version and new-version must be set together")
        return [Upgrade(package, old, new)]
    if not dependencies_json.strip():
        raise ActionError(
            "nothing to check: set updated-dependencies-json to the output of "
            "dependabot/fetch-metadata, or set package, old-version and new-version"
        )
    try:
        updates = json.loads(dependencies_json)
    except json.JSONDecodeError as error:
        raise ActionError(f"updated-dependencies-json is not valid JSON: {error}") from error
    if not isinstance(updates, list) or not all(isinstance(update, dict) for update in updates):
        raise ActionError("updated-dependencies-json must be a JSON array of objects")

    upgrades: list[Upgrade] = []
    for update in updates:
        if update.get("packageEcosystem") not in ECOSYSTEMS:
            continue
        upgrade = Upgrade(*(
            str(update.get(key) or "") for key in ("dependencyName", "prevVersion", "newVersion")
        ))
        if upgrade not in upgrades:
            upgrades.append(upgrade)
    return upgrades


def _error_report(upgrade: Upgrade, reason: str) -> dict[str, Any]:
    return {
        "package": upgrade.package,
        "old": upgrade.old,
        "new": upgrade.new,
        "findings": [],
        "summary": {"breaking": 0, "review": 0, "status": "error"},
        "error": reason,
    }


def _is_report(report: object) -> bool:
    """Whether output follows the CLI's JSON schema, so rendering cannot fail."""
    if not isinstance(report, dict):
        return False
    findings, summary, error = report.get("findings"), report.get("summary"), report.get("error")
    return (
        isinstance(findings, list)
        and all(
            isinstance(finding, dict)
            and isinstance(finding.get("file"), str)
            and type(finding.get("line")) is int
            and finding.get("severity") in _SEVERITIES
            and isinstance(finding.get("message"), str)
            for finding in findings
        )
        and isinstance(summary, dict)
        and type(summary.get("breaking")) is int
        and type(summary.get("review")) is int
        and summary.get("status") in _STATUSES
        and (error is None or isinstance(error, str))
    )


def check(upgrade: Upgrade, path: str) -> dict[str, Any]:
    """Run the CLI for one upgrade; any failure becomes an error report."""
    missing = [
        name
        for name, value in (
            ("package name", upgrade.package),
            ("old version", upgrade.old),
            ("new version", upgrade.new),
        )
        if not value
    ]
    if missing:
        return _error_report(upgrade, f"the update has no {' or '.join(missing)}")

    # "--" keeps a value that starts with "-" from being read as an option.
    command = [
        sys.executable, "-m", "upgrade_impact", "--format", "json", "--",
        upgrade.package, upgrade.old, upgrade.new, path,
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _error_report(upgrade, f"timed out after {TIMEOUT_SECONDS // 60} minutes")
    except OSError as error:
        return _error_report(upgrade, f"could not run upgrade-impact: {error}")

    try:
        report = json.loads(process.stdout)
    except ValueError:
        report = None
    if not _is_report(report):
        output = " ".join((process.stderr or process.stdout).split())
        reason = f"upgrade-impact exited with code {process.returncode} without a report"
        return _error_report(upgrade, f"{reason}: {output}" if output else reason)
    # Show the names that were requested, even for an argument error.
    return {**report, "package": upgrade.package, "old": upgrade.old, "new": upgrade.new}


def _boolean(value: str, name: str) -> bool:
    # The same values as the toolkit's core.getBooleanInput.
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False
    raise ActionError(f"{name} must be true or false, not {value!r}")


def _command(name: str, message: str) -> str:
    """Format a workflow command; escaping keeps the message on one line."""
    data = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{name}::{data}"


def _blob_url(env: Mapping[str, str]) -> str | None:
    """Return the URL prefix of the checked commit's files, if it is known."""
    server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = env.get("GITHUB_REPOSITORY", "")
    commit = env.get("HEAD_SHA", "")
    if not repository or not _COMMIT.fullmatch(commit):
        return None
    return f"{server}/{repository}/blob/{commit}"


def _root(env: Mapping[str, str], path: str) -> str | None:
    """Return the scanned directory relative to the workspace, or None if outside it."""
    workspace = Path(env.get("GITHUB_WORKSPACE") or Path.cwd()).resolve()
    try:
        relative = (Path.cwd() / path).resolve().relative_to(workspace)
    except ValueError:
        return None
    return "" if relative == Path() else relative.as_posix()


def _write_outputs(env: Mapping[str, str], outputs: Mapping[str, str]) -> None:
    output_file = env.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as stream:
            stream.writelines(f"{name}={value}\n" for name, value in outputs.items())


def main(environ: Mapping[str, str] | None = None) -> int:
    """Check every requested upgrade and write the comment for the comment step."""
    env = os.environ if environ is None else environ
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        if env.get("GITHUB_EVENT_NAME") == "pull_request_target":
            raise ActionError(
                "upgrade-impact must not run on pull_request_target; trigger it with pull_request"
            )
        fail_on_breaking = _boolean(env.get("INPUT_FAIL_ON_BREAKING", "true"), "fail-on-breaking")
        path = env.get("INPUT_PATH") or "."
        if not Path(path).is_dir():
            raise ActionError(f"path is not a directory: {path}")
        upgrades = select_upgrades(
            env.get("INPUT_UPDATED_DEPENDENCIES_JSON", ""),
            env.get("INPUT_PACKAGE", "").strip(),
            env.get("INPUT_OLD_VERSION", "").strip(),
            env.get("INPUT_NEW_VERSION", "").strip(),
        )
    except ActionError as error:
        print(_command("error", str(error)))
        return 1

    outputs = {"comment-file": "", "breaking": "0", "fail": "false"}
    if not upgrades:
        print("upgrade-impact: no pip or uv updates to check")
    else:
        reports = []
        for upgrade in upgrades:
            report = check(upgrade, path)
            reports.append(report)
            # One line that cannot start a workflow command.
            line = f"{upgrade.package} {upgrade.old} → {upgrade.new}: {describe(report)}"
            print("upgrade-impact:", " ".join(line.split()))
        body = render(reports, blob_url=_blob_url(env), root=_root(env, path))
        comment_file = Path(env.get("RUNNER_TEMP") or tempfile.gettempdir()) / "upgrade-impact" / "comment.md"
        comment_file.parent.mkdir(parents=True, exist_ok=True)
        comment_file.write_text(body, encoding="utf-8", newline="\n")
        breaking = sum(report["summary"]["breaking"] for report in reports)
        outputs = {
            "comment-file": comment_file.as_posix(),
            "breaking": str(breaking),
            "fail": "true" if fail_on_breaking and breaking else "false",
        }
    _write_outputs(env, outputs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
