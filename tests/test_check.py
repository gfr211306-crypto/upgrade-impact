from pathlib import Path

import griffe
import platformdirs
import pytest

from upgrade_impact.check import Finding, check, check_usages
from upgrade_impact.scan import Usage


def module(name: str, *members: griffe.Object | griffe.Alias) -> griffe.Module:
    result = griffe.Module(name)
    for member in members:
        result.set_member(member.name, member)
    collection = griffe.ModulesCollection()
    collection.set_member(name, result)
    return result


def usage(path: str, line: int = 1, kind: str = "import", file: str = "app.py") -> Usage:
    return Usage(Path(file), line, path, kind)


def assert_removed(findings: list[Finding], expected: list[Usage]) -> None:
    assert [(item.file, item.line, item.path) for item in findings] == [
        (item.file, item.line, item.path) for item in expected
    ]
    for finding in findings:
        assert finding.severity == "breaking"
        assert finding.path in finding.message
        assert "removed" in finding.message.lower()


@pytest.mark.parametrize("kind", ["import", "call"])
def test_reports_removed_member_for_imports_and_calls(kind: str) -> None:
    old = module("pkg", griffe.Function("removed"))
    new = module("pkg")
    target = usage("pkg.removed", kind=kind)

    assert_removed(check_usages([target], old, new), [target])


def test_unchanged_root_and_members_have_no_findings() -> None:
    old = module("pkg", module("util", griffe.Function("keep")))
    new = module("pkg", module("util", griffe.Function("keep")))
    usages = [usage("pkg"), usage("pkg.util", 2), usage("pkg.util.keep", 3, "call")]

    assert check_usages(usages, old, new) == []


@pytest.mark.parametrize("present_in_new", [False, True])
def test_member_missing_from_old_is_skipped(present_in_new: bool) -> None:
    old = module("pkg")
    new = module("pkg", griffe.Function("added")) if present_in_new else module("pkg")

    assert check_usages([usage("pkg.added", kind="call")], old, new) == []


def test_removed_submodule_and_its_members_are_reported() -> None:
    old = module("pkg", module("util", griffe.Function("removed")))
    new = module("pkg")
    usages = [usage("pkg.util"), usage("pkg.util.removed", 2, "call")]

    assert_removed(check_usages(usages, old, new), usages)


def test_missing_parent_in_old_is_skipped() -> None:
    old = module("pkg")
    new = module("pkg")

    assert check_usages([usage("pkg.missing.child", kind="call")], old, new) == []


@pytest.mark.parametrize("reexport_kept", [False, True])
def test_checks_the_public_reexport_path(reexport_kept: bool) -> None:
    old_target = griffe.Function("Markup")
    new_target = griffe.Function("Markup")
    old = module("pkg", module("utils", old_target), griffe.Alias("Markup", old_target))
    new = module("pkg", module("utils", new_target))
    if reexport_kept:
        new.set_member("Markup", griffe.Alias("Markup", new_target))
    target = usage("pkg.Markup")

    assert_removed(check_usages([target], old, new), [] if reexport_kept else [target])


def test_resolves_intermediate_alias_to_walk_members() -> None:
    old_target = module("internal", griffe.Function("removed"))
    new_target = module("internal")
    old = module("pkg", old_target, griffe.Alias("public", old_target))
    new = module("pkg", new_target, griffe.Alias("public", new_target))
    target = usage("pkg.public.removed", kind="call")

    assert_removed(check_usages([target], old, new), [target])


def test_terminal_unresolved_alias_still_counts_as_present_in_old() -> None:
    old = module("pkg", griffe.Alias("removed", "external.removed"))
    new = module("pkg")
    target = usage("pkg.removed")

    assert_removed(check_usages([target], old, new), [target])


def test_terminal_unresolved_alias_in_new_is_not_a_removal() -> None:
    old = module("pkg", griffe.Function("keep"))
    new = module("pkg", griffe.Alias("keep", "external.keep"))

    assert check_usages([usage("pkg.keep", kind="call")], old, new) == []


@pytest.mark.parametrize("unknown_version", ["old", "new"])
@pytest.mark.parametrize("cyclic", [False, True], ids=["unresolved", "cyclic"])
def test_unknown_intermediate_alias_does_not_claim_removal(
    unknown_version: str, cyclic: bool
) -> None:
    unknown = module("pkg")
    if cyclic:
        unknown.set_member("util", griffe.Alias("util", "pkg.other"))
        unknown.set_member("other", griffe.Alias("other", "pkg.util"))
    else:
        unknown.set_member("util", griffe.Alias("util", "external.util"))
    present = module("pkg", module("util", griffe.Function("method")))
    absent = module("pkg")
    old, new = (unknown, absent) if unknown_version == "old" else (present, unknown)

    assert check_usages([usage("pkg.util.method", kind="call")], old, new) == []


def test_one_finding_per_file_and_line_in_sorted_order() -> None:
    old = module("pkg", griffe.Function("outer"), griffe.Function("inner"))
    new = module("pkg")
    usages = [
        usage("pkg.inner", 3, "call", "z.py"),
        usage("pkg.outer", 7, "call", "a.py"),
        usage("pkg.inner", 7, "call", "a.py"),
        usage("pkg.outer", 2, "import", "a.py"),
        usage("pkg.outer", 2, "call", "a.py"),
    ]

    assert_removed(check_usages(iter(usages), old, new), [usages[3], usages[1], usages[0]])


def test_filters_unrelated_package_names() -> None:
    old = module("pkg", griffe.Function("removed"))
    new = module("pkg")
    usages = [usage("pkg_extra.removed"), usage("other.pkg.removed", 2)]

    assert check_usages(usages, old, new) == []


@pytest.mark.parametrize(
    ("distribution", "override", "expected_import"),
    [("Foo-Bar", None, "foo_bar"), ("PyYAML", "yaml", "yaml")],
)
def test_check_loads_versions_after_creating_cache_and_scans_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    distribution: str,
    override: str | None,
    expected_import: str,
) -> None:
    cache = tmp_path / "cache" / "griffe"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        f"from {expected_import} import removed as target\ntarget()\n",
        encoding="utf-8",
    )
    old = module(expected_import, griffe.Function("removed"))
    new = module(expected_import)
    calls = []

    def fake_cache_dir(appname: str) -> str:
        assert appname == "griffe"
        return str(cache)

    def fake_load_pypi(import_name: str, dist_name: str, version: str) -> griffe.Module:
        assert cache.is_dir(), "The griffe cache must exist before loading either version"
        calls.append((import_name, dist_name, version))
        return {"==1.2.3": old, "==2.0.0": new}[version]

    monkeypatch.setattr(platformdirs, "user_cache_dir", fake_cache_dir)
    monkeypatch.setattr(griffe, "load_pypi", fake_load_pypi)

    findings = check(distribution, "1.2.3", "2.0.0", repo, import_name=override)

    assert calls == [
        (expected_import, distribution, "==1.2.3"),
        (expected_import, distribution, "==2.0.0"),
    ]
    assert_removed(
        findings,
        [usage(f"{expected_import}.removed"), usage(f"{expected_import}.removed", 2, "call")],
    )
