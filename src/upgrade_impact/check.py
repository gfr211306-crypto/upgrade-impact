"""Compare dependency versions against the repository's actual usages."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import griffe
import platformdirs

from upgrade_impact.scan import Usage, scan


class CheckError(RuntimeError):
    """A dependency could not be loaded for comparison."""


@dataclass(frozen=True)
class Finding:
    """A compatibility finding at a repository-relative source location."""

    file: Path
    line: int
    path: str
    message: str
    severity: Literal["breaking", "review"] = "breaking"


def _exists(root: griffe.Module, path: str) -> bool | None:
    """Return None when an intermediate alias prevents a reliable lookup."""
    if path == root.path:
        return True
    prefix = root.path + "."
    if not path.startswith(prefix):
        return False
    current = root
    try:
        for part in path[len(prefix):].split("."):
            current = current.members[part]
    except KeyError:
        return False
    except (griffe.AliasResolutionError, griffe.CyclicAliasError):
        return None
    # Do not resolve a terminal alias: an external re-export still exists
    # even when its target dependency was not installed by load_pypi.
    return True


def check_usages(
    usages: Iterable[Usage], old: griffe.Module, new: griffe.Module
) -> list[Finding]:
    """Report proven removals, keeping one finding per source line.

    Paths missing in the old version, or blocked by unresolved intermediate
    aliases in either version, cannot establish a removal and are skipped.
    """
    findings: dict[tuple[Path, int], Finding] = {}
    for usage in usages:
        if _exists(old, usage.path) is True and _exists(new, usage.path) is False:
            key = (usage.file, usage.line)
            findings.setdefault(
                key,
                Finding(
                    usage.file, usage.line, usage.path, f"{usage.path} was removed"
                ),
            )
    return sorted(findings.values(), key=lambda finding: (finding.file.as_posix(), finding.line))


def check(
    distribution: str,
    old_version: str,
    new_version: str,
    repo_path: str | Path,
    import_name: str | None = None,
) -> list[Finding]:
    """Load two exact PyPI versions and check imports and calls in a repository."""
    package = import_name if import_name is not None else distribution.lower().replace("-", "_")
    usages = scan(repo_path, package)
    Path(platformdirs.user_cache_dir("griffe")).mkdir(parents=True, exist_ok=True)

    def load(version: str) -> griffe.Module:
        try:
            return griffe.load_pypi(package, distribution, f"=={version}")
        except Exception as error:
            # The loader wraps pip, archive extraction, and Python analysis;
            # all loader failures must become tool errors, not breakages.
            raise CheckError(f"Could not load {distribution}=={version}: {error}") from error

    old = load(old_version)
    new = load(new_version)
    return check_usages(usages, old, new)
