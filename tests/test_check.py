from pathlib import Path

import griffe
import platformdirs
import pytest

from upgrade_impact.check import CheckError, Finding, check, check_usages
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


def function(
    name: str, *parameters: griffe.Parameter, labels: tuple[str, ...] = ()
) -> griffe.Function:
    result = griffe.Function(name, parameters=griffe.Parameters(*parameters))
    result.labels.update(labels)
    return result


def param(name: str, default: str | None = None) -> griffe.Parameter:
    return griffe.Parameter(name, kind=griffe.ParameterKind.positional_or_keyword, default=default)


def klass(
    name: str, *members: griffe.Object | griffe.Alias, bases: tuple[griffe.Expr, ...] = ()
) -> griffe.Class:
    result = griffe.Class(name, bases=list(bases))
    for member in members:
        result.set_member(member.name, member)
    return result


def call(path: str, *keywords: str, positional: int = 0, line: int = 1) -> Usage:
    return Usage(Path("app.py"), line, path, "call", frozenset(keywords), positional)


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


def test_removed_parameter_passed_by_keyword_is_breaking() -> None:
    old = module("pkg", function("fetch", param("url"), param("legacy", "None")))
    new = module("pkg", function("fetch", param("url")))

    assert check_usages([call("pkg.fetch", "legacy", positional=1)], old, new) == [
        Finding(Path("app.py"), 1, "pkg.fetch", "pkg.fetch(...) uses removed parameter `legacy=`"),
    ]


@pytest.mark.parametrize(("positional", "expected"), [(2, ["review"]), (1, [])])
def test_removed_parameter_is_judged_by_position_when_not_passed_by_keyword(
    positional: int, expected: list[str]
) -> None:
    old = module("pkg", function("fetch", param("url"), param("legacy", "None")))
    new = module("pkg", function("fetch", param("url")))

    findings = check_usages([call("pkg.fetch", positional=positional)], old, new)

    assert [finding.severity for finding in findings] == expected
    assert all("`legacy`" in finding.message for finding in findings)


@pytest.mark.parametrize(
    ("keywords", "positional", "expected"),
    [((), 1, ["review"]), (("mode",), 1, []), ((), 2, [])],
    ids=["default-used", "passed-by-keyword", "passed-by-position"],
)
def test_changed_default_needs_review_only_when_the_call_relies_on_it(
    keywords: tuple[str, ...], positional: int, expected: list[str]
) -> None:
    old = module("pkg", function("fetch", param("url"), param("mode", "'fast'")))
    new = module("pkg", function("fetch", param("url"), param("mode", "'safe'")))

    findings = check_usages([call("pkg.fetch", *keywords, positional=positional)], old, new)

    assert [finding.severity for finding in findings] == expected
    for finding in findings:
        assert finding.message == (
            "pkg.fetch(...) relies on the default of `mode`, which changed from `'fast'` to `'safe'`"
        )


@pytest.mark.parametrize(
    ("path", "positional", "expected"),
    [
        ("pkg.Client", 0, ["review"]),
        ("pkg.Client", 1, []),
        ("pkg.Client.create", 0, ["review"]),
        ("pkg.Client.create", 1, []),
    ],
)
def test_implicit_self_and_cls_are_not_counted_as_positional_arguments(
    path: str, positional: int, expected: list[str]
) -> None:
    def version(default: str) -> griffe.Module:
        return module(
            "pkg",
            klass(
                "Client",
                function("__init__", param("self"), param("mode", default)),
                function("create", param("cls"), param("mode", default), labels=("classmethod",)),
            ),
        )

    findings = check_usages([call(path, positional=positional)], version("1"), version("2"))

    assert [finding.severity for finding in findings] == expected


def test_class_call_checks_constructor_through_reexport_alias() -> None:
    def version(*parameters: griffe.Parameter) -> griffe.Module:
        target = klass("Retry", function("__init__", param("self"), *parameters))
        return module("pkg", module("retry", target), griffe.Alias("Retry", target))

    old = version(param("total", "10"), param("method_whitelist", "None"))
    new = version(param("total", "10"))
    usages = [call("pkg.Retry", "method_whitelist", positional=1), call("pkg.retry.Retry", line=2)]

    assert check_usages(usages, old, new) == [
        Finding(
            Path("app.py"), 1, "pkg.Retry", "pkg.Retry(...) uses removed parameter `method_whitelist=`"
        ),
    ]


def test_class_call_checks_inherited_constructor() -> None:
    def version(*parameters: griffe.Parameter) -> griffe.Module:
        result = module("pkg", klass("Base", function("__init__", param("self"), *parameters)))
        result.set_member("Child", klass("Child", bases=(griffe.ExprName("Base", parent=result),)))
        return result

    old = version(param("legacy", "None"))
    new = version()

    findings = check_usages([call("pkg.Child", "legacy")], old, new)

    assert [(finding.severity, finding.message) for finding in findings] == [
        ("breaking", "pkg.Child(...) uses removed parameter `legacy=`"),
    ]


def test_signature_breakages_are_reported_before_class_breakages() -> None:
    old = module(
        "pkg",
        klass(
            "Retry",
            function("__init__", param("self"), param("mode", "1")),
            bases=(griffe.ExprName("Base"),),
        ),
    )
    new = module("pkg", klass("Retry", function("__init__", param("self"), param("mode", "2"))))

    findings = check_usages([call("pkg.Retry")], old, new)

    assert [finding.message for finding in findings] == [
        "pkg.Retry(...) relies on the default of `mode`, which changed from `1` to `2`",
    ]


@pytest.mark.parametrize(
    ("new_member", "message"),
    [
        (
            function("fetch", param("url"), param("timeout")),
            "pkg.fetch(...) may break: parameter was added as required (`timeout`)",
        ),
        (
            klass("fetch"),
            "pkg.fetch(...) may break: public object points to a different kind of object",
        ),
    ],
    ids=["added-required", "changed-kind"],
)
def test_other_breakages_of_called_objects_need_review(
    new_member: griffe.Object, message: str
) -> None:
    old = module("pkg", function("fetch", param("url")))
    new = module("pkg", new_member)

    assert check_usages([call("pkg.fetch", positional=1)], old, new) == [
        Finding(Path("app.py"), 1, "pkg.fetch", message, "review"),
    ]


def test_changed_attribute_values_are_ignored() -> None:
    old = module("pkg", griffe.Attribute("handler", value="make('a')"))
    new = module("pkg", griffe.Attribute("handler", value="make('b')"))

    assert check_usages([call("pkg.handler")], old, new) == []


def test_imports_and_unrelated_calls_skip_signature_findings() -> None:
    old = module(
        "pkg",
        function("fetch", param("legacy", "None")),
        function("other", param("legacy", "None")),
    )
    new = module("pkg", function("fetch"), function("other", param("legacy", "None")))

    assert check_usages([usage("pkg.fetch"), call("pkg.other", "legacy")], old, new) == []


@pytest.mark.parametrize("breaking_first", [False, True])
def test_breaking_signature_finding_wins_on_the_same_line(breaking_first: bool) -> None:
    old = module(
        "pkg",
        function("PoolManager", param("retries", "3")),
        function("Retry", param("method_whitelist", "None")),
    )
    new = module("pkg", function("PoolManager", param("retries", "4")), function("Retry"))
    usages = [call("pkg.PoolManager"), call("pkg.Retry", "method_whitelist")]
    if breaking_first:
        usages.reverse()

    findings = check_usages(usages, old, new)

    assert [(finding.path, finding.severity) for finding in findings] == [("pkg.Retry", "breaking")]


def test_first_review_finding_wins_on_the_same_line() -> None:
    old = module("pkg", function("first", param("mode", "1")), function("second", param("mode", "1")))
    new = module("pkg", function("first", param("mode", "2")), function("second", param("mode", "2")))

    findings = check_usages([call("pkg.second"), call("pkg.first")], old, new)

    assert [(finding.path, finding.severity) for finding in findings] == [("pkg.second", "review")]


def test_api_comparison_runs_only_for_calls_to_surviving_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(old: griffe.Module, new: griffe.Module) -> None:
        raise AssertionError("APIs were compared without a call to check")

    monkeypatch.setattr(griffe, "find_breaking_changes", fail)
    old = module("pkg", function("kept"), function("removed"))
    new = module("pkg", function("kept"))
    usages = [usage("pkg.kept"), call("pkg.removed", line=2), call("pkg.added", line=3)]

    assert_removed(check_usages(usages, old, new), [usages[1]])


def test_api_comparison_alias_errors_are_check_errors() -> None:
    old = module("pkg", function("fetch"), module("util"))
    new = module("pkg", function("fetch"))
    new.set_member("util", griffe.Alias("util", "pkg.other"))
    new.set_member("other", griffe.Alias("other", "pkg.util"))

    with pytest.raises(CheckError, match="Could not compare the pkg APIs"):
        check_usages([call("pkg.fetch")], old, new)


@pytest.mark.parametrize(
    ("old_bases", "new_bases", "expected"),
    [
        (("object",), (), []),
        (("Base", "object"), ("Base",), []),
        (("Base", "Mixin"), ("Base",), ["pkg.Retry(...) may break: base class was removed"]),
    ],
    ids=["only-object", "object-and-kept-base", "real-base"],
)
def test_removing_only_the_object_base_is_ignored(
    old_bases: tuple[str, ...], new_bases: tuple[str, ...], expected: list[str]
) -> None:
    def version(bases: tuple[str, ...]) -> griffe.Module:
        return module("pkg", klass("Retry", bases=tuple(griffe.ExprName(base) for base in bases)))

    findings = check_usages([call("pkg.Retry")], version(old_bases), version(new_bases))

    assert [finding.message for finding in findings] == expected


@pytest.mark.parametrize(
    ("keywords", "positional", "expected"),
    [
        ((), 2, ["pkg.fetch(...) may break: positional parameter was moved (`timeout`)"]),
        ((), 1, []),
        (("timeout",), 1, []),
    ],
    ids=["passed-by-position", "not-passed", "passed-by-keyword"],
)
def test_moved_parameter_needs_review_only_when_passed_by_position(
    keywords: tuple[str, ...], positional: int, expected: list[str]
) -> None:
    old = module("pkg", function("fetch", param("url"), param("timeout", "1"), param("retries", "0")))
    new = module("pkg", function("fetch", param("url"), param("retries", "0"), param("timeout", "1")))

    findings = check_usages([call("pkg.fetch", *keywords, positional=positional)], old, new)

    assert [finding.message for finding in findings] == expected


def fake_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception | None = None
) -> Path:
    """Serve two empty package versions, or raise ``error`` from the loader."""
    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda appname: str(tmp_path / "cache"))

    def load_pypi(import_name: str, dist_name: str, version: str) -> griffe.Module:
        if error is not None:
            raise error
        return module(import_name)

    monkeypatch.setattr(griffe, "load_pypi", load_pypi)
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def test_class_hierarchy_errors_during_comparison_are_check_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fake_versions(monkeypatch, tmp_path)

    def fail(*args: object) -> None:
        raise ValueError("Cannot compute C3 linearization")

    monkeypatch.setattr("upgrade_impact.check.check_usages", fail)

    with pytest.raises(CheckError, match=(
        r"^Could not compare pkg 1\.0 and 2\.0: Cannot compute C3 linearization$"
    )):
        check("pkg", "1.0", "2.0", repo)


def test_inconsistent_class_hierarchies_do_not_crash_the_comparison() -> None:
    def version(default: str) -> griffe.Module:
        result = module("pkg")
        base = klass("Base", function("__init__", param("self"), param("mode", default)))
        result.set_member("Base", base)
        result.set_member("Child", klass("Child", bases=(griffe.ExprName("Base", parent=result),)))
        # Base(Child) and Child(Base) form an inheritance cycle: no MRO exists.
        base.bases.append(griffe.ExprName("Child", parent=result))
        result.set_member("Mixed", klass("Mixed", bases=(
            griffe.ExprName("Base", parent=result), griffe.ExprName("Child", parent=result),
        )))
        return result

    usages = [call("pkg.Base"), call("pkg.Child", line=2), call("pkg.Mixed", line=3)]

    findings = check_usages(usages, version("1"), version("2"))

    assert [(finding.line, finding.severity) for finding in findings] == [(1, "review")]
