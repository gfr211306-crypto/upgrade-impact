"""Compare dependency versions against the repository's actual usages."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import griffe
import platformdirs

from upgrade_impact.scan import Usage, scan


class CheckError(RuntimeError):
    """A dependency could not be loaded or compared."""


@dataclass(frozen=True)
class Finding:
    """A compatibility finding at a repository-relative source location."""

    file: Path
    line: int
    path: str
    message: str
    severity: Literal["breaking", "review"] = "breaking"


_ALIAS_ERRORS = (griffe.AliasResolutionError, griffe.CyclicAliasError)
_POSITIONAL = frozenset(
    {griffe.ParameterKind.positional_only, griffe.ParameterKind.positional_or_keyword}
)
_Breakages = dict[str, list[griffe.Breakage]]


def _member(root: griffe.Module, path: str) -> griffe.Object | griffe.Alias | None:
    """Walk ``.members`` to ``path``, returning None when a member is missing.

    Raises griffe's alias errors when an intermediate alias prevents a
    reliable lookup.
    """
    if path == root.path:
        return root
    prefix = root.path + "."
    if not path.startswith(prefix):
        return None
    current = root
    try:
        for part in path[len(prefix):].split("."):
            current = current.members[part]
    except KeyError:
        return None
    # Do not resolve a terminal alias: an external re-export still exists
    # even when its target dependency was not installed by load_pypi.
    return current


def _final(obj: griffe.Object | griffe.Alias) -> griffe.Object:
    return obj.final_target if obj.is_alias else obj


def _callee(obj: griffe.Object | griffe.Alias) -> tuple[griffe.Object, griffe.Object | None]:
    """Return the called object and the function that binds the call's arguments."""
    target = _final(obj)
    function = target
    if target.is_class:
        init = target.all_members.get("__init__")
        function = None if init is None else _final(init)
    return target, function if function is not None and function.is_function else None


def _index_breakages(old: griffe.Module, new: griffe.Module) -> _Breakages:
    index: _Breakages = {}
    try:
        for breakage in griffe.find_breaking_changes(old, new):
            # Changed attribute values are mostly noise, such as __version__.
            if breakage.kind is not griffe.BreakageKind.ATTRIBUTE_CHANGED_VALUE:
                index.setdefault(breakage.obj.path, []).append(breakage)
    except _ALIAS_ERRORS as error:
        raise CheckError(f"Could not compare the {old.path} APIs: {error}") from error
    return index


def _judge(
    usage: Usage, breakage: griffe.Breakage, positional: list[str]
) -> tuple[Literal["breaking", "review"], str] | None:
    """Judge one breakage of a called object against the call's arguments."""
    parameters = [
        value
        for value in (breakage.old_value, breakage.new_value)
        if isinstance(value, griffe.Parameter)
    ]
    name = parameters[0].name if parameters else None
    by_keyword = name in usage.keywords
    by_position = name in positional[:usage.positional_args]
    call = f"{usage.path}(...)"
    if breakage.kind is griffe.BreakageKind.PARAMETER_REMOVED:
        if by_keyword:
            return "breaking", f"{call} uses removed parameter `{name}=`"
        if by_position:
            return "review", f"{call} may pass removed parameter `{name}` by position"
        return None
    if breakage.kind is griffe.BreakageKind.PARAMETER_CHANGED_DEFAULT:
        if by_keyword or by_position:
            return None
        old_default, new_default = (parameter.default for parameter in parameters)
        return "review", (
            f"{call} relies on the default of `{name}`,"
            f" which changed from `{old_default}` to `{new_default}`"
        )
    if breakage.kind is griffe.BreakageKind.PARAMETER_MOVED and not by_position:
        return None
    if breakage.kind is griffe.BreakageKind.CLASS_REMOVED_BASE:
        kept = {str(base) for base in breakage.new_value}
        removed = {str(base) for base in breakage.old_value} - kept
        # Dropping an explicit ``object`` base changes nothing in Python 3.
        if removed <= {"object"}:
            return None
    detail = f" (`{name}`)" if name else ""
    return "review", f"{call} may break: {breakage.kind.value.lower()}{detail}"


def _check_call(
    usage: Usage,
    old_obj: griffe.Object | griffe.Alias,
    new_obj: griffe.Object | griffe.Alias,
    breakages: _Breakages,
) -> Finding | None:
    """Return the most severe signature finding for a call, if any."""
    try:
        old_target, old_function = _callee(old_obj)
        new_target, new_function = _callee(new_obj)
    except _ALIAS_ERRORS:
        return None

    # Parameters the call can fill by position, judged against the old
    # signature the code was written for; ``self`` and ``cls`` are bound.
    positional: list[str] = []
    if old_function is not None:
        parameters = list(old_function.parameters)
        if old_target.is_class or "classmethod" in old_function.labels:
            parameters = parameters[1:]
        positional = [parameter.name for parameter in parameters if parameter.kind in _POSITIONAL]

    # Most breakages are keyed by the new object's path, removals by the old
    # one. Signature breakages say the most about a call, so they come first.
    paths = dict.fromkeys(
        obj.path
        for obj in (old_function, new_function, old_target, new_target)
        if obj is not None
    )
    finding = None
    for path in paths:
        for breakage in breakages.get(path, ()):
            judged = _judge(usage, breakage, positional)
            if judged is None:
                continue
            severity, message = judged
            if severity == "breaking":
                return Finding(usage.file, usage.line, usage.path, message, severity)
            if finding is None:
                finding = Finding(usage.file, usage.line, usage.path, message, severity)
    return finding


def check_usages(
    usages: Iterable[Usage], old: griffe.Module, new: griffe.Module
) -> list[Finding]:
    """Report removals and call signature changes, one finding per source line.

    Paths missing in the old version, or blocked by unresolved intermediate
    aliases in either version, cannot establish a breakage and are skipped.
    On each line the first breaking finding wins, then the first to review.
    """
    findings: dict[tuple[Path, int], Finding] = {}
    breakages: _Breakages | None = None
    for usage in usages:
        try:
            old_obj = _member(old, usage.path)
            new_obj = _member(new, usage.path)
        except _ALIAS_ERRORS:
            continue
        if old_obj is None:
            continue
        if new_obj is None:
            finding = Finding(usage.file, usage.line, usage.path, f"{usage.path} was removed")
        elif usage.kind == "call":
            # Compare whole APIs lazily: only calls to surviving objects need it.
            if breakages is None:
                breakages = _index_breakages(old, new)
            finding = _check_call(usage, old_obj, new_obj, breakages)
        else:
            continue
        if finding is None:
            continue
        key = (usage.file, usage.line)
        current = findings.get(key)
        if current is None or (current.severity, finding.severity) == ("review", "breaking"):
            findings[key] = finding
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
