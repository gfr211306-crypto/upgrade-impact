import io
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import griffe
import pytest

from upgrade_impact import wheels


def write_wheel(path: Path, files: dict[str, str]) -> None:
    with ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)


def extract(tmp_path: Path, files: dict[str, str]) -> tuple[tuple[str, ...], Path]:
    wheel = tmp_path / "pkg-1-py3-none-any.whl"
    directory = tmp_path / "sources"
    directory.mkdir()
    write_wheel(wheel, files)
    return wheels._extract(wheel, directory), directory


def test_top_level_metadata_takes_precedence_over_record(tmp_path: Path) -> None:
    names, directory = extract(tmp_path, {
        "pkg-1.dist-info/top_level.txt": "yaml\n_other\nyaml\nnot valid\n",
        "pkg-1.dist-info/RECORD": "unrelated.py,,\n",
        "yaml/__init__.py": "def safe_load(stream): pass\n",
    })

    assert names == ("_other", "yaml")
    assert (directory / "yaml" / "__init__.py").is_file()


def test_record_discovers_modules_packages_namespace_roots_and_spread_libraries(
    tmp_path: Path,
) -> None:
    files = {
        "pkg-1.dist-info/RECORD": (
            "dateutil/__init__.py,,\n"
            "bs4/element.py,,\n"
            "single.py,,\n"
            "stub.pyi,,\n"
            "native.cp310-win_amd64.pyd,,\n"
            "namespace/sub/module.py,,\n"
            "pkg-1.data/purelib/yaml/__init__.py,,\n"
            "pkg-1.data/platlib/extension.abi3.so,,\n"
            "pkg-1.data/scripts/tool.py,,\n"
            "pkg-1.dist-info/METADATA,,\n"
            "data/readme.txt,,\n"
            "LICENSE,,\n"
        ),
        "pkg-1.data/purelib/yaml/__init__.py": "VALUE = 1\n",
        "pkg-1.data/scripts/tool.py": "raise RuntimeError('script')\n",
        "single.py": "VALUE = 1\n",
    }

    names, directory = extract(tmp_path, files)

    assert names == ("bs4", "dateutil", "extension", "namespace", "native", "single", "stub", "yaml")
    assert (directory / "yaml" / "__init__.py").is_file()
    assert not (directory / "pkg-1.data").exists()


def test_empty_top_level_file_falls_back_to_record(tmp_path: Path) -> None:
    names, _ = extract(tmp_path, {
        "pkg-1.dist-info/top_level.txt": "\n",
        "pkg-1.dist-info/RECORD": "module.py,,\n",
        "module.py": "VALUE = 1\n",
    })

    assert names == ("module",)


def test_record_parsing_handles_csv_quoted_paths(tmp_path: Path) -> None:
    names, _ = extract(tmp_path, {
        "pkg-1.dist-info/RECORD": '"pkg/resource,one.txt",,\nmodule.py,,\n',
        "module.py": "VALUE = 1\n",
    })

    assert names == ("module",)


@pytest.mark.parametrize("path", ["../escape.py", "/escape.py", "C:/escape.py", "..\\escape.py"])
def test_unsafe_archive_paths_are_rejected_before_extraction(tmp_path: Path, path: str) -> None:
    with pytest.raises(wheels.WheelError, match="Unsafe path in wheel"):
        extract(tmp_path, {"pkg-1.dist-info/top_level.txt": "pkg\n", path: "VALUE = 1\n"})


def test_unsafe_record_paths_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(wheels.WheelError, match="Unsafe path in wheel"):
        extract(tmp_path, {"pkg-1.dist-info/RECORD": "../../escape.py,,\n"})


def test_missing_import_metadata_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(wheels.WheelError, match="neither top_level.txt nor RECORD"):
        extract(tmp_path, {"pkg/__init__.py": "VALUE = 1\n"})


def test_loading_never_executes_source_or_extracts_pth(tmp_path: Path) -> None:
    names, directory = extract(tmp_path, {
        "pkg-1.dist-info/top_level.txt": "pkg\n",
        "pkg/__init__.py": "raise RuntimeError('package code was run')\ndef fetch(value): pass\n",
        "pkg.pth": "import os; raise RuntimeError('pth code was run')\n",
        "pkg/native.pyd": "binary placeholder",
    })
    distribution = wheels.WheelDistribution("pkg", "1", directory, names)

    loaded = distribution.load("pkg")

    assert loaded.is_module
    assert "fetch" in loaded.members
    assert not (directory / "pkg.pth").exists()
    assert not (directory / "pkg" / "native.pyd").exists()


def test_griffe_loading_explicitly_disables_dynamic_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    calls = []

    def load(target: str, **options: object) -> griffe.Module:
        calls.append((target, options))
        return griffe.Module("pkg")

    monkeypatch.setattr(griffe, "load", load)

    wheels.WheelDistribution("pkg", "1", tmp_path, ("pkg",)).load("pkg")

    assert calls == [("pkg", {
        "search_paths": [tmp_path],
        "try_relative_path": False,
        "allow_inspection": False,
        "force_inspection": False,
    })]


def test_standalone_stubs_can_be_loaded(tmp_path: Path) -> None:
    (tmp_path / "pkg.pyi").write_text("def fetch(value: str) -> str: ...\n", encoding="utf-8")

    loaded = wheels.WheelDistribution("pkg", "1", tmp_path, ("pkg",)).load("pkg")

    assert loaded.path == "pkg"
    assert "fetch" in loaded.members


def test_compiled_only_or_missing_import_is_a_clear_tool_error(tmp_path: Path) -> None:
    distribution = wheels.WheelDistribution("pkg", "1", tmp_path, ("pkg",))

    with pytest.raises(wheels.WheelError, match="no Python source or stubs"):
        distribution.load("pkg")


def test_has_import_includes_binary_paths_omitted_from_top_level_metadata(tmp_path: Path) -> None:
    wheel = tmp_path / "pkg-1-py3-none-any.whl"
    directory = tmp_path / "sources"
    directory.mkdir()
    write_wheel(wheel, {
        "pkg-1.dist-info/top_level.txt": "declared\n",
        "pkg/__init__.py": "VALUE = 1\n",
        "pkg/sub/__init__.pyi": "VALUE: int\n",
        "pkg/native.cp310-win_amd64.pyd": "binary placeholder",
        "other.abi3.so": "binary placeholder",
        "namespace/sub/module.py": "VALUE = 1\n",
    })
    import_paths: set[str] = set()
    names = wheels._extract(wheel, directory, import_paths)
    distribution = wheels.WheelDistribution("pkg", "1", directory, names, frozenset(import_paths))

    assert names == ("declared",)
    for import_name in ["pkg", "pkg.sub", "pkg.native", "other", "namespace", "namespace.sub.module"]:
        assert distribution.has_import(import_name)
    assert not distribution.has_import("pkg.missing")
    assert not distribution.has_import("pkg.native.child")
    assert not distribution.has_import("declared")


def release(monkeypatch: pytest.MonkeyPatch, files: list[dict[str, str]]) -> list[str]:
    requested = []

    def urlopen(url: str, timeout: int) -> io.BytesIO:
        assert timeout == 30
        requested.append(url)
        return io.BytesIO(json.dumps({"urls": files}).encode())

    monkeypatch.setattr(wheels, "urlopen", urlopen)
    return requested


def wheel_info(filename: str) -> dict[str, str]:
    return {
        "filename": filename,
        "url": f"https://files.pythonhosted.org/{filename}",
        "packagetype": "bdist_wheel",
    }


def test_release_selects_a_universal_wheel_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = release(monkeypatch, [
        wheel_info("pkg-1-cp310-cp310-win_amd64.whl"),
        wheel_info("pkg-1-py2.py3-none-any.whl"),
        {"filename": "pkg-1.tar.gz", "packagetype": "sdist"},
    ])

    assert wheels._release_wheel("Some Package", "1+local") == (
        "pkg-1-py2.py3-none-any.whl",
        "https://files.pythonhosted.org/pkg-1-py2.py3-none-any.whl",
    )
    assert requested == ["https://pypi.org/pypi/Some%20Package/1%2Blocal/json"]


def test_release_prefers_python_three_sources_over_a_python_two_only_wheel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release(monkeypatch, [
        wheel_info("pkg-1-py2-none-any.whl"),
        wheel_info("pkg-1-cp310-cp310-win_amd64.whl"),
    ])

    assert wheels._release_wheel("pkg", "1")[0] == "pkg-1-cp310-cp310-win_amd64.whl"


def test_source_only_release_is_rejected_without_running_pip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release(monkeypatch, [{"filename": "pkg-1.tar.gz", "packagetype": "sdist"}])

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("pip must never receive a source distribution")

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(wheels.WheelError, match="No wheel is available for pkg==1"):
        with wheels.download_distribution("pkg", "1"):
            pass


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("MarkupSafe-2.0.1-cp310-cp310-win_amd64.whl", ("310", "cp", "cp310", "win_amd64")),
        ("pkg-1-py2.py3-none-any.whl", (f"{sys.version_info.major}.{sys.version_info.minor}", "py", "none", "any")),
        ("pkg-1-cp39-cp39-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", ("39", "cp", "cp39", "manylinux_2_17_x86_64")),
    ],
)
def test_wheel_cross_target_tags(filename: str, expected: tuple[str, ...]) -> None:
    assert wheels._wheel_tags(filename) == expected


def test_download_is_binary_only_cross_target_and_temporary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filename = "MarkupSafe-2.0.1-cp310-cp310-win_amd64.whl"
    url = f"https://files.pythonhosted.org/{filename}"
    monkeypatch.setattr(wheels, "_release_wheel", lambda distribution, version: (filename, url))
    monkeypatch.setenv("PIP_ONLY_BINARY", ":none:")
    calls = []

    def run(arguments: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append((arguments, options))
        destination = Path(arguments[arguments.index("--dest") + 1])
        write_wheel(destination / filename, {
            "MarkupSafe-2.0.1.dist-info/top_level.txt": "markupsafe\n",
            "markupsafe/__init__.py": "def soft_unicode(value): pass\n",
        })
        return subprocess.CompletedProcess(arguments, 0, stdout="Downloaded wheel")

    monkeypatch.setattr(subprocess, "run", run)

    with wheels.download_distribution("MarkupSafe", "2.0.1") as distribution:
        directory = distribution.directory
        assert distribution.names == ("markupsafe",)
        assert "soft_unicode" in distribution.load("markupsafe").members
        assert directory.is_dir()

    assert not directory.exists()
    arguments, options = calls[0]
    assert arguments[:4] == [sys.executable, "-m", "pip", "download"]
    assert "--no-deps" in arguments
    assert "--only-binary=:all:" in arguments
    assert "--ignore-requires-python" in arguments
    assert arguments[-1] == url
    for option, value in [
        ("--python-version", "310"), ("--implementation", "cp"),
        ("--abi", "cp310"), ("--platform", "win_amd64"),
    ]:
        assert arguments[arguments.index(option) + 1] == value
    assert options["env"]["PIP_ONLY_BINARY"] == ":all:"
    assert options["stdout"] == subprocess.PIPE
    assert options["stderr"] == subprocess.STDOUT


def test_download_failure_keeps_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    filename = "pkg-1-py3-none-any.whl"
    monkeypatch.setattr(wheels, "_release_wheel", lambda distribution, version: (
        filename, f"https://files.pythonhosted.org/{filename}",
    ))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 1, stdout="Network unavailable",
    ))

    with pytest.raises(wheels.WheelError, match="Could not download wheel for pkg==1: Network unavailable"):
        with wheels.download_distribution("pkg", "1"):
            pass
