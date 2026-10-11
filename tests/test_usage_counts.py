"""Count scanned source locations independently of compatibility findings."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import griffe
import pytest

from upgrade_impact.check import check
from upgrade_impact.scan import SKIP_DIRS


def module(name: str, *members: griffe.Object) -> griffe.Module:
    result = griffe.Module(name)
    for member in members:
        result.set_member(member.name, member)
    collection = griffe.ModulesCollection()
    collection.set_member(name, result)
    return result


def serve_wheels(monkeypatch, old, new, *, names=None):
    """Serve wheel APIs without downloading or importing any package code."""
    loaded = []

    @contextmanager
    def download(distribution, version):
        modules = old if version == "1" else new

        def load(name):
            loaded.append((version, name))
            return modules[name]

        yield SimpleNamespace(
            names=tuple(modules) if names is None else names,
            load=load,
            has_import=lambda name: name in modules,
        )

    monkeypatch.setattr("upgrade_impact.check.download_distribution", download)
    return loaded


def unchanged_package(monkeypatch):
    serve_wheels(
        monkeypatch,
        {"pkg": module("pkg", griffe.Function("run"), griffe.Function("other"))},
        {"pkg": module("pkg", griffe.Function("run"), griffe.Function("other"))},
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import pkg; pkg.run(); pkg.run()\n", 1),
        ("from pkg import run, other\nrun(other()); run()\n", 2),
        ("import pkg\npkg.run(pkg.other())\n", 2),
    ],
    ids=["import-and-calls", "multiple-imports-and-calls", "nested-calls"],
)
def test_each_source_line_counts_once(tmp_path, monkeypatch, source, expected):
    unchanged_package(monkeypatch)
    (tmp_path / "app.py").write_text(source, encoding="utf-8")

    result = check("pkg", "1", "2", tmp_path)

    assert (result.usages, result.files) == (expected, 1)
    assert result.findings == []


def test_usage_locations_are_deduplicated_across_import_names(tmp_path, monkeypatch):
    serve_wheels(
        monkeypatch,
        {name: module(name, griffe.Function("run")) for name in ("first", "second")},
        {name: module(name, griffe.Function("run")) for name in ("first", "second")},
    )
    (tmp_path / "app.py").write_text(
        "import first, second\nfirst.run(); second.run()\n", encoding="utf-8"
    )

    result = check("multi-root", "1", "2", tmp_path)

    assert (result.usages, result.files) == (2, 1)
    assert result.findings == []


def test_same_line_number_in_different_files_counts_separately(tmp_path, monkeypatch):
    unchanged_package(monkeypatch)
    (tmp_path / "nested").mkdir()
    for path in (tmp_path / "app.py", tmp_path / "nested" / "app.py"):
        path.write_text("import pkg\npkg.run()\n", encoding="utf-8")

    result = check("pkg", "1", "2", tmp_path)

    assert (result.usages, result.files) == (4, 2)


def test_import_without_a_call_or_finding_still_counts(tmp_path, monkeypatch):
    unchanged_package(monkeypatch)
    (tmp_path / "app.py").write_text("from pkg import run\n", encoding="utf-8")

    result = check("pkg", "1", "2", tmp_path)

    assert (result.usages, result.files) == (1, 1)
    assert result.findings == []


def test_call_missing_from_old_api_still_counts(tmp_path, monkeypatch):
    unchanged_package(monkeypatch)
    (tmp_path / "app.py").write_text("import pkg\npkg.missing()\n", encoding="utf-8")

    result = check("pkg", "1", "2", tmp_path)

    assert (result.usages, result.files) == (2, 1)
    assert result.findings == []


def test_new_only_import_name_still_counts_scanned_locations(tmp_path, monkeypatch):
    loaded = serve_wheels(monkeypatch, {}, {"added": module("added", griffe.Function("run"))})
    (tmp_path / "app.py").write_text("import added\nadded.run()\n", encoding="utf-8")

    result = check("pkg", "1", "2", tmp_path)

    assert (result.usages, result.files) == (2, 1)
    assert result.imported
    assert result.findings == []
    assert loaded == []


@pytest.mark.parametrize("override", [None, "pkg"])
def test_not_imported_has_zero_counts(tmp_path, monkeypatch, override):
    loaded = serve_wheels(monkeypatch, {"pkg": module("pkg")}, {"pkg": module("pkg")})
    (tmp_path / "app.py").write_text("import unrelated\n", encoding="utf-8")

    result = check("pkg", "1", "2", tmp_path, import_name=override)

    assert (result.usages, result.files) == (0, 0)
    assert not result.imported
    assert result.findings == []
    assert loaded == []


def test_explicit_override_counts_only_the_requested_import(tmp_path, monkeypatch):
    serve_wheels(
        monkeypatch,
        {name: module(name, griffe.Function("run")) for name in ("pkg", "other")},
        {name: module(name, griffe.Function("run")) for name in ("pkg", "other")},
    )
    (tmp_path / "app.py").write_text(
        "import pkg\nimport other\npkg.run()\nother.run()\n", encoding="utf-8"
    )
    (tmp_path / "other.py").write_text("import other\nother.run()\n", encoding="utf-8")

    result = check("multi-root", "1", "2", tmp_path, import_name="pkg")

    assert (result.usages, result.files) == (2, 1)


def test_dotted_override_counts_only_that_submodule(tmp_path, monkeypatch):
    def api():
        root = module("pkg", module("tools", griffe.Function("run")))
        return root.members["tools"]

    loaded = serve_wheels(
        monkeypatch, {"pkg.tools": api()}, {"pkg.tools": api()}, names=("pkg",)
    )
    (tmp_path / "app.py").write_text(
        "import pkg\nimport pkg.tools as tools\npkg.run()\ntools.run()\n", encoding="utf-8"
    )

    result = check("pkg", "1", "2", tmp_path, import_name="pkg.tools")

    assert (result.usages, result.files) == (2, 1)
    assert loaded == [("1", "pkg.tools"), ("2", "pkg.tools")]


def test_skipped_unparseable_and_unrelated_files_do_not_count(tmp_path, monkeypatch):
    unchanged_package(monkeypatch)
    (tmp_path / "app.py").write_text("import pkg\npkg.run()\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("import pkg\nif :\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("import unrelated\nunrelated.run()\n", encoding="utf-8")
    for name in SKIP_DIRS:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "hidden.py").write_text("import pkg\npkg.run()\n", encoding="utf-8")

    result = check("pkg", "1", "2", tmp_path)

    assert (result.usages, result.files) == (2, 1)
