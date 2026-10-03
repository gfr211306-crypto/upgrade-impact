"""Command-line interface: argument validation, output, and exit codes."""

import argparse
from pathlib import Path
import re
import sys

from upgrade_impact.check import CheckError, check


EXIT_OK = 0
EXIT_BREAKING = 1
EXIT_ERROR = 2

# PEP 508 distribution names.
_DISTRIBUTION = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")
# An exact version such as 1.26.15 or 2.0.0rc1, not a specifier such as >=2.0.
_VERSION = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.!+_-]*[A-Za-z0-9])?")

_EPILOG = """\
example:
  upgrade-impact urllib3 1.26.15 2.0.0 ./my-repo

output:
  one line per finding, then a summary:
    ❌ file:line  message   the upgrade breaks this line
    ⚠️ file:line  message   review this line

exit codes:
  0  no breaking findings
  1  at least one breaking finding
  2  tool error (invalid arguments, package not found, ...)
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upgrade-impact",
        description=(
            "Find the lines in your Python code that break when you upgrade one dependency.\n"
            "Both versions are downloaded from PyPI and their real APIs are compared."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("distribution", help="distribution name on PyPI, such as urllib3 or PyYAML")
    parser.add_argument("old_version", help="exact version you use now, such as 1.26.15")
    parser.add_argument("new_version", help="exact version you want to upgrade to, such as 2.0.0")
    parser.add_argument("repo_path", type=Path, help="directory of the repository to scan")
    parser.add_argument(
        "--import-name",
        metavar="NAME",
        help=(
            "module name used in import statements, when it differs from the distribution name "
            "(default: the distribution name lowercased, with - replaced by _; "
            "example: --import-name yaml for PyYAML)"
        ),
    )
    return parser


def _validate(args: argparse.Namespace) -> str | None:
    """Return an error message for invalid arguments, before any download."""
    if not _DISTRIBUTION.fullmatch(args.distribution):
        return f"invalid distribution name: {args.distribution!r}"
    for label, version in (("old", args.old_version), ("new", args.new_version)):
        if not _VERSION.fullmatch(version):
            return f"invalid {label} version {version!r}: give an exact version such as 1.26.15"
    if args.old_version == args.new_version:
        return f"old and new versions are both {args.old_version}"
    if args.import_name is not None and not all(
        part.isidentifier() for part in args.import_name.split(".")
    ):
        return f"invalid import name: {args.import_name!r}"
    if not args.repo_path.exists():
        return f"repository path does not exist: {args.repo_path}"
    if not args.repo_path.is_dir():
        return f"repository path is not a directory: {args.repo_path}"
    return None


def main(argv: list[str] | None = None) -> int:
    """Print findings and return the documented process exit code."""
    # Emoji and non-ASCII paths must not crash a cp950 or cp1252 console,
    # or a redirected file on Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = _parser().parse_args(argv)
    problem = _validate(args)
    if problem is not None:
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_ERROR

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
        return EXIT_ERROR
    except Exception as error:
        # An uncaught exception would exit with 1, which means "breaking".
        print(f"error: unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_ERROR

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
    return EXIT_BREAKING if breaking else EXIT_OK
