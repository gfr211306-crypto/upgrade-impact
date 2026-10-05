"""Download wheels and inspect their Python sources without running package code."""

from contextlib import contextmanager
import csv
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Iterator
from urllib.parse import quote, urlsplit
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

import griffe


class WheelError(RuntimeError):
    """A distribution could not be downloaded or analyzed safely."""


@dataclass(frozen=True)
class WheelDistribution:
    """A downloaded distribution's import names and extracted static sources."""

    distribution: str
    version: str
    directory: Path
    names: tuple[str, ...]
    import_paths: frozenset[str] = frozenset()

    def has_import(self, import_name: str) -> bool:
        """Whether the wheel contains a source, stub, or binary import path."""
        if import_name in self.import_paths:
            return True
        target = self.directory.joinpath(*import_name.split("."))
        return target.is_dir() or target.with_suffix(".py").is_file() or target.with_suffix(".pyi").is_file()

    def load(self, import_name: str) -> griffe.Module:
        """Load an API with dynamic inspection explicitly disabled."""
        root = import_name.split(".", 1)[0]
        package_dir = self.directory / root
        module_file = self.directory / f"{root}.py"
        stub_file = self.directory / f"{root}.pyi"
        if not (package_dir.is_dir() or module_file.is_file() or stub_file.is_file()):
            raise WheelError(
                f"Could not load {self.distribution}=={self.version}: "
                f"import {import_name!r} has no Python source or stubs in its wheel "
                "(compiled modules cannot be inspected safely)"
            )
        try:
            # Griffe's package finder supports package stubs, but cannot load
            # standalone .pyi modules; visit their syntax directly instead.
            if import_name == root and stub_file.is_file() and not module_file.is_file():
                collection = griffe.ModulesCollection()
                loaded = griffe.visit(
                    root, stub_file, stub_file.read_text(encoding="utf-8-sig"),
                    modules_collection=collection,
                )
                collection.set_member(root, loaded)
                return loaded
            loaded = griffe.load(
                import_name,
                search_paths=[self.directory],
                try_relative_path=False,
                allow_inspection=False,
                force_inspection=False,
            )
            if not loaded.is_module:
                raise ValueError(f"{import_name!r} is not a module")
            return loaded
        except Exception as error:
            raise WheelError(
                f"Could not load {self.distribution}=={self.version} "
                f"as {import_name}: {error}"
            ) from error


def _wheel_tags(filename: str) -> tuple[str, str, str, str]:
    """Select one concrete interpreter/ABI/platform from a wheel filename."""
    fields = filename.removesuffix(".whl").rsplit("-", 3)
    if len(fields) != 4 or not filename.endswith(".whl"):
        raise WheelError(f"Invalid wheel filename: {filename}")
    _, interpreters, abis, platforms = fields
    candidates = []
    for tag in interpreters.split("."):
        match = re.fullmatch(r"([a-z]+)(\d+)", tag)
        if match:
            implementation, digits = match.groups()
            candidates.append((digits.startswith("3"), int(digits), implementation, digits))
    if not candidates:
        raise WheelError(f"Invalid interpreter tag in wheel: {filename}")
    _, _, implementation, digits = max(candidates)
    python_version = digits
    if digits == "3":
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return python_version, implementation, abis.split(".")[0], platforms.split(".")[0]


def _release_wheel(distribution: str, version: str) -> tuple[str, str]:
    """Choose a deterministic wheel, preferring a universal Python wheel."""
    metadata_url = (
        f"https://pypi.org/pypi/{quote(distribution, safe='')}/{quote(version, safe='')}/json"
    )
    with urlopen(metadata_url, timeout=30) as response:
        metadata = json.load(response)
    files = metadata.get("urls", [])
    candidates = [
        item for item in files
        if item.get("packagetype") == "bdist_wheel"
        and item.get("filename", "").endswith(".whl")
    ]
    if not candidates:
        raise WheelError(
            f"No wheel is available for {distribution}=={version}; "
            "source distributions are disabled to avoid running build code"
        )

    def priority(item: dict[str, object]) -> tuple[bool, bool, str]:
        filename = str(item["filename"])
        python_version, implementation, abi, platform = _wheel_tags(filename)
        universal = implementation == "py" and abi == "none" and platform == "any"
        return (not python_version.startswith("3"), not universal, filename)

    chosen = min(candidates, key=priority)
    filename, url = chosen["filename"], chosen["url"]
    if not isinstance(filename, str) or not isinstance(url, str) or urlsplit(url).scheme != "https":
        raise WheelError(f"Invalid wheel metadata for {distribution}=={version}")
    return filename, url


def _archive_path(name: str) -> PurePosixPath:
    """Reject paths that could escape extraction on Unix or Windows."""
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or ":" in name
        or not path.parts
    ):
        raise WheelError(f"Unsafe path in wheel: {name!r}")
    return path


def _import_path(path: PurePosixPath) -> PurePosixPath | None:
    """Map wheel files to import locations, including spread wheel libraries."""
    if path.parts[0].endswith(".dist-info"):
        return None
    if path.parts[0].endswith(".data"):
        if len(path.parts) < 3 or path.parts[1] not in {"purelib", "platlib"}:
            return None
        return PurePosixPath(*path.parts[2:])
    return path


def _record_names(record: str) -> tuple[str, ...]:
    names = set()
    for row in csv.reader(io.StringIO(record)):
        if not row:
            continue
        path = _import_path(_archive_path(row[0]))
        if path is None or path.suffix not in {".py", ".pyi", ".pyd", ".so"}:
            continue
        root = path.parts[0] if len(path.parts) > 1 else path.name.split(".", 1)[0]
        if root.isidentifier():
            names.add(root)
    return tuple(sorted(names))


def _extract(
    wheel: Path, directory: Path, import_paths: set[str] | None = None,
) -> tuple[str, ...]:
    """Extract only Python sources/stubs; discover imports from wheel metadata."""
    with ZipFile(wheel) as archive:
        paths = {info.filename: _archive_path(info.filename) for info in archive.infolist()}
        top_levels = [
            name for name, path in paths.items()
            if len(path.parts) == 2 and path.parts[0].endswith(".dist-info")
            and path.name == "top_level.txt"
        ]
        names = set()
        for name in top_levels:
            for line in archive.read(name).decode("utf-8-sig").splitlines():
                root = line.strip().split(".", 1)[0]
                if root.isidentifier():
                    names.add(root)
        if not names:
            records = [
                name for name, path in paths.items()
                if len(path.parts) == 2 and path.parts[0].endswith(".dist-info")
                and path.name == "RECORD"
            ]
            if not records:
                raise WheelError("The wheel contains neither top_level.txt nor RECORD import metadata")
            for name in records:
                names.update(_record_names(archive.read(name).decode("utf-8-sig")))
        for name, path in paths.items():
            target_path = _import_path(path)
            if target_path is None:
                continue
            if import_paths is not None and target_path.suffix in {".py", ".pyi", ".so", ".pyd", ".pyc", ".pyo"}:
                parts = [*target_path.parts[:-1], target_path.name.split(".", 1)[0]]
                if parts[-1] == "__init__":
                    parts.pop()
                if parts and all(part.isidentifier() for part in parts):
                    for end in range(1, len(parts) + 1):
                        import_paths.add(".".join(parts[:end]))
            if target_path.suffix not in {".py", ".pyi"}:
                continue
            target = directory.joinpath(*target_path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
        return tuple(sorted(names))


@contextmanager
def download_distribution(distribution: str, version: str) -> Iterator[WheelDistribution]:
    """Yield a distribution backed by a wheel, then remove its temporary files."""
    with TemporaryDirectory(prefix="upgrade-impact-") as temporary:
        directory = Path(temporary)
        wheelhouse = directory / "wheels"
        sources = directory / "sources"
        wheelhouse.mkdir()
        sources.mkdir()
        try:
            filename, url = _release_wheel(distribution, version)
            python_version, implementation, abi, platform = _wheel_tags(filename)
            environment = os.environ.copy()
            environment["PIP_ONLY_BINARY"] = ":all:"
            process = subprocess.run(
                [
                    sys.executable, "-m", "pip", "download", "--no-deps",
                    "--only-binary=:all:", "--no-input", "--disable-pip-version-check",
                    "--ignore-requires-python",
                    "--dest", str(wheelhouse),
                    "--python-version", python_version, "--implementation", implementation,
                    "--abi", abi, "--platform", platform,
                    url,
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if process.returncode:
                detail = process.stdout.strip()
                raise WheelError(f"Could not download wheel for {distribution}=={version}: {detail}")
            wheel = wheelhouse / filename
            if not wheel.is_file():
                raise WheelError(f"The wheel download did not produce {filename}")
            import_paths: set[str] = set()
            names = _extract(wheel, sources, import_paths)
        except WheelError:
            raise
        except (OSError, ValueError, KeyError, TypeError, BadZipFile) as error:
            raise WheelError(f"Could not download {distribution}=={version}: {error}") from error
        yield WheelDistribution(distribution, version, sources, names, frozenset(import_paths))
