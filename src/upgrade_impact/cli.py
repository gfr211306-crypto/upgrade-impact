"""Command-line interface: argument validation, output, and exit codes."""

import argparse
import json
from pathlib import Path
import re
import sys

from upgrade_impact.check import CheckError, Finding, check


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


class _JSONArgumentError(ValueError):
    """An argument error that can be reported using the JSON schema."""


class _ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *, json_errors: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.json_errors = json_errors

    def error(self, message: str) -> None:
        if self.json_errors:
            raise _JSONArgumentError(message)
        super().error(message)


def _parser(*, json_errors: bool = False) -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        json_errors=json_errors,
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
            "override import name; by default, auto-detect names from wheel metadata "
            "and analyze the ones imported by the repository"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format (default: text); json writes one result object, including errors",
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


def _json_requested(argv: list[str]) -> bool:
    """Recognize JSON output even when argparse cannot parse all arguments."""
    output_format = "text"
    for index, argument in enumerate(argv):
        if argument.startswith("--format="):
            output_format = argument.split("=", 1)[1]
        elif argument == "--format" and index + 1 < len(argv):
            output_format = argv[index + 1]
    return output_format == "json"


def _print_json(
    args: argparse.Namespace,
    findings: list[Finding],
    *,
    status: str,
    error: str | None = None,
) -> None:
    print(json.dumps({
        "package": getattr(args, "distribution", None),
        "old": getattr(args, "old_version", None),
        "new": getattr(args, "new_version", None),
        "findings": [
            {
                "file": finding.file.as_posix(),
                "line": finding.line,
                "severity": finding.severity,
                "message": finding.message,
            }
            for finding in findings
        ],
        "summary": {
            "breaking": sum(finding.severity == "breaking" for finding in findings),
            "review": sum(finding.severity == "review" for finding in findings),
            "status": status,
        },
        "error": error,
    }, ensure_ascii=False))


def _report_error(args: argparse.Namespace, message: str, *, json_output: bool) -> int:
    if json_output:
        _print_json(args, [], status="error", error=message)
    else:
        print(f"error: {message}", file=sys.stderr)
    return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    """Print findings and return the documented process exit code."""
    # Emoji and non-ASCII paths must not crash a cp950 or cp1252 console,
    # or a redirected file on Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    arguments = list(sys.argv[1:] if argv is None else argv)
    json_output = _json_requested(arguments)
    args = argparse.Namespace()
    try:
        args = _parser(json_errors=json_output).parse_args(arguments, namespace=args)
    except _JSONArgumentError as error:
        return _report_error(args, str(error), json_output=True)
    json_output = args.format == "json"
    problem = _validate(args)
    if problem is not None:
        return _report_error(args, problem, json_output=json_output)

    try:
        result = check(
            args.distribution,
            args.old_version,
            args.new_version,
            args.repo_path,
            import_name=args.import_name,
        )
    except (CheckError, OSError) as error:
        return _report_error(args, str(error), json_output=json_output)
    except Exception as error:
        # An uncaught exception would exit with 1, which means "breaking".
        return _report_error(
            args, f"unexpected {type(error).__name__}: {error}", json_output=json_output,
        )

    findings = result.findings
    breaking = sum(finding.severity == "breaking" for finding in findings)
    review = sum(finding.severity == "review" for finding in findings)
    if json_output:
        _print_json(args, findings, status="ok" if result.imported else "not imported")
    else:
        if not result.imported:
            print("not imported")
        for finding in findings:
            icon = "❌" if finding.severity == "breaking" else "⚠️"
            print(f"{icon} {finding.file.as_posix()}:{finding.line}  {finding.message}")
        print(f"{breaking} breaking · {review} to review")
    return EXIT_BREAKING if breaking else EXIT_OK
