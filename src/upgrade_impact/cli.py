"""Minimal command-line interface for dependency existence checks."""

import argparse
from pathlib import Path
import sys

from upgrade_impact.check import CheckError, check


def main(argv: list[str] | None = None) -> int:
    """Print findings and return the documented process exit code."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="upgrade-impact",
        description="Find dependency upgrade breakages in your Python code.",
    )
    parser.add_argument("distribution")
    parser.add_argument("old_version")
    parser.add_argument("new_version")
    parser.add_argument("repo_path", type=Path)
    parser.add_argument("--import-name")
    args = parser.parse_args(argv)

    try:
        findings = check(
            args.distribution,
            args.old_version,
            args.new_version,
            args.repo_path,
            import_name=args.import_name,
        )
    except (CheckError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    breaking = 0
    review = 0
    for finding in findings:
        if finding.severity == "breaking":
            breaking += 1
            icon = "❌"
        else:
            review += 1
            icon = "⚠️"
        print(f"{icon} {finding.file.as_posix()}:{finding.line}  {finding.message}")
    print(f"{breaking} breaking · {review} to review")
    return 1 if breaking else 0
