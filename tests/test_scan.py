from pathlib import Path

import pytest

from upgrade_impact.scan import Usage, scan


def write_source(repo: Path, relative_path: str, source: str) -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "import_path", "call_path"),
    [
        ("import urllib3\nurllib3.Retry()\n", "urllib3", "urllib3.Retry"),
        (
            "import urllib3.util.retry\nurllib3.util.retry.Retry()\n",
            "urllib3.util.retry",
            "urllib3.util.retry.Retry",
        ),
        (
            "import urllib3.util.retry as retry\nretry.Retry()\n",
            "urllib3.util.retry",
            "urllib3.util.retry.Retry",
        ),
        (
            "from urllib3.util.retry import Retry as R\nR()\n",
            "urllib3.util.retry.Retry",
            "urllib3.util.retry.Retry",
        ),
        (
            "from urllib3 import Retry\nRetry()\n",
            "urllib3.Retry",
            "urllib3.Retry",
        ),
    ],
)
def test_import_and_alias_mapping(
    tmp_path: Path, source: str, import_path: str, call_path: str
) -> None:
    write_source(tmp_path, "app.py", source)

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 1, import_path, "import"),
        Usage(Path("app.py"), 2, call_path, "call"),
    ]


def test_dotted_import_without_alias_binds_the_top_level_name(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "app.py",
        "import urllib3.util.retry\nurllib3.Retry()\nretry.Retry()\n",
    )

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 1, "urllib3.util.retry", "import"),
        Usage(Path("app.py"), 2, "urllib3.Retry", "call"),
    ]


def test_records_all_names_imported_on_one_line(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "app.py",
        "import urllib3, urllib3.util as util\n"
        "from urllib3 import Retry as R, PoolManager\n"
        "R(); PoolManager(); util.Retry()\n",
    )

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 1, "urllib3", "import"),
        Usage(Path("app.py"), 1, "urllib3.util", "import"),
        Usage(Path("app.py"), 2, "urllib3.Retry", "import"),
        Usage(Path("app.py"), 2, "urllib3.PoolManager", "import"),
        Usage(Path("app.py"), 3, "urllib3.Retry", "call"),
        Usage(Path("app.py"), 3, "urllib3.PoolManager", "call"),
        Usage(Path("app.py"), 3, "urllib3.util.Retry", "call"),
    ]


def test_filters_by_package_boundary(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "app.py",
        "import urllib3_extra\n"
        "from other import urllib3 as unrelated\n"
        "from urllib3_extra import Retry\n"
        "urllib3_extra.Retry()\n"
        "unrelated.Retry()\n"
        "Retry()\n"
        "import urllib3 as u\n"
        "u.Retry()\n",
    )

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 7, "urllib3", "import"),
        Usage(Path("app.py"), 8, "urllib3.Retry", "call"),
    ]


def test_relative_imports_do_not_resolve_as_external_package(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "app.py",
        "from . import urllib3\n"
        "from .urllib3 import Retry\n"
        "from ..urllib3 import PoolManager\n"
        "urllib3.Retry()\nRetry()\nPoolManager()\n",
    )

    assert scan(tmp_path, "urllib3") == []


def test_star_import_does_not_guess_call_targets(tmp_path: Path) -> None:
    write_source(tmp_path, "app.py", "from urllib3 import *\nRetry()\n")

    assert all(usage.kind != "call" for usage in scan(tmp_path, "urllib3"))


def test_records_nested_calls_and_explicit_argument_counts(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "app.py",
        "import urllib3\n"
        "urllib3.PoolManager(\n"
        "    retries=urllib3.Retry(1, *args, 2, total=3, **kwargs),\n"
        "    timeout=5,\n"
        ")\n",
    )

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 1, "urllib3", "import"),
        Usage(
            Path("app.py"),
            2,
            "urllib3.PoolManager",
            "call",
            frozenset({"retries", "timeout"}),
        ),
        Usage(
            Path("app.py"),
            3,
            "urllib3.Retry",
            "call",
            frozenset({"total"}),
            2,
        ),
    ]


@pytest.mark.parametrize(
    ("import_name", "expected"),
    [
        (
            "markupsafe",
            [
                Usage(Path("app.py"), 1, "markupsafe.soft_unicode", "import"),
                Usage(
                    Path("app.py"),
                    9,
                    "markupsafe.soft_unicode",
                    "call",
                    positional_args=1,
                ),
            ],
        ),
        (
            "jinja2",
            [
                Usage(Path("app.py"), 2, "jinja2.Markup", "import"),
                Usage(Path("app.py"), 2, "jinja2.Environment", "import"),
                Usage(Path("app.py"), 8, "jinja2.Environment", "call"),
                Usage(
                    Path("app.py"), 10, "jinja2.Markup", "call", positional_args=1
                ),
            ],
        ),
        (
            "urllib3",
            [
                Usage(Path("app.py"), 3, "urllib3", "import"),
                Usage(Path("app.py"), 4, "urllib3.util.retry.Retry", "import"),
                Usage(
                    Path("app.py"),
                    6,
                    "urllib3.util.retry.Retry",
                    "call",
                    frozenset({"total", "method_whitelist"}),
                ),
                Usage(
                    Path("app.py"),
                    7,
                    "urllib3.PoolManager",
                    "call",
                    frozenset({"retries"}),
                ),
                Usage(
                    Path("app.py"),
                    7,
                    "urllib3.Retry",
                    "call",
                    frozenset({"method_whitelist"}),
                    1,
                ),
            ],
        ),
    ],
)
def test_acceptance_fixture_usages(import_name: str, expected: list[Usage]) -> None:
    fixture_repo = Path(__file__).parent / "fixtures" / "demo"

    assert scan(fixture_repo, import_name) == expected


def test_recursive_scan_is_deterministic_and_accepts_a_string_path(tmp_path: Path) -> None:
    for filename in ("z.py", "pkg/b.py", "pkg/a.py", "a.py"):
        write_source(tmp_path, filename, "import urllib3\nurllib3.Retry()\n")
    write_source(tmp_path, "notes.txt", "import urllib3\nurllib3.Retry()\n")

    assert scan(str(tmp_path), "urllib3") == [
        usage
        for filename in ("a.py", "pkg/a.py", "pkg/b.py", "z.py")
        for usage in (
            Usage(Path(filename), 1, "urllib3", "import"),
            Usage(Path(filename), 2, "urllib3.Retry", "call"),
        )
    ]


@pytest.mark.parametrize(
    "directory", [".venv", "venv", ".git", "node_modules", "build", "dist", "site-packages"]
)
def test_skips_excluded_directories_at_any_depth(tmp_path: Path, directory: str) -> None:
    write_source(tmp_path, f"{directory}/app.py", "import urllib3\n")
    write_source(tmp_path, f"pkg/{directory}/app.py", "import urllib3\n")
    write_source(tmp_path, f"{directory}_source/app.py", "import urllib3\n")

    assert scan(tmp_path, "urllib3") == [
        Usage(Path(f"{directory}_source/app.py"), 1, "urllib3", "import")
    ]


def test_skips_unparseable_files_and_keeps_scanning(tmp_path: Path) -> None:
    write_source(tmp_path, "broken.py", "import urllib3\nif (\n")
    write_source(tmp_path, "valid.py", "import urllib3\n")

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("valid.py"), 1, "urllib3", "import")
    ]


def test_reads_python_encoding_cookie(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_bytes(
        '# coding: latin-1\n# caf\xe9\nimport urllib3\nurllib3.Retry()\n'.encode("latin-1")
    )

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 3, "urllib3", "import"),
        Usage(Path("app.py"), 4, "urllib3.Retry", "call"),
    ]


def test_import_mappings_do_not_leak_between_files(tmp_path: Path) -> None:
    write_source(tmp_path, "a.py", "import urllib3 as u\nu.Retry()\n")
    write_source(tmp_path, "b.py", "u.Retry()\n")

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("a.py"), 1, "urllib3", "import"),
        Usage(Path("a.py"), 2, "urllib3.Retry", "call"),
    ]


def test_local_imports_do_not_leak_to_module_or_sibling_functions(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "app.py",
        "def first():\n"
        "    from urllib3 import Retry as factory\n"
        "    factory()\n"
        "def second():\n"
        "    factory()\n"
        "factory()\n",
    )

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 2, "urllib3.Retry", "import"),
        Usage(Path("app.py"), 3, "urllib3.Retry", "call"),
    ]


def test_module_import_is_available_in_function(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "app.py",
        "import urllib3 as u\ndef build_retry():\n    return u.Retry()\n",
    )

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 1, "urllib3", "import"),
        Usage(Path("app.py"), 3, "urllib3.Retry", "call"),
    ]


def test_scans_source_without_executing_it(tmp_path: Path) -> None:
    write_source(tmp_path, "app.py", 'raise RuntimeError("do not run")\nimport urllib3\n')

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("app.py"), 2, "urllib3", "import")
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import urllib3 as u\ndef build(u):\n    u.Retry()\n",
            [Usage(Path("app.py"), 1, "urllib3", "import")],
        ),
        (
            "import urllib3 as u\n"
            "def build():\n"
            "    u.Retry()\n"
            "    u = other\n",
            [Usage(Path("app.py"), 1, "urllib3", "import")],
        ),
        (
            "import urllib3 as u\nu = u.Retry()\nu.Retry()\n",
            [
                Usage(Path("app.py"), 1, "urllib3", "import"),
                Usage(Path("app.py"), 2, "urllib3.Retry", "call"),
            ],
        ),
        (
            "import urllib3 as u\nfrom . import u\nu.Retry()\n",
            [Usage(Path("app.py"), 1, "urllib3", "import")],
        ),
        (
            "import urllib3 as u\n"
            "class Client:\n"
            "    from urllib3 import Retry as R\n"
            "    def build(self):\n"
            "        R()\n"
            "        u.Retry()\n",
            [
                Usage(Path("app.py"), 1, "urllib3", "import"),
                Usage(Path("app.py"), 3, "urllib3.Retry", "import"),
                Usage(Path("app.py"), 6, "urllib3.Retry", "call"),
            ],
        ),
        (
            "from urllib3 import Retry\n"
            "[Retry() for Retry in factories]\n"
            "Retry()\n",
            [
                Usage(Path("app.py"), 1, "urllib3.Retry", "import"),
                Usage(Path("app.py"), 3, "urllib3.Retry", "call"),
            ],
        ),
        (
            "class Outer:\n"
            "    import urllib3 as u\n"
            "    class Inner:\n"
            "        u.Retry()\n",
            [Usage(Path("app.py"), 2, "urllib3", "import")],
        ),
        (
            "import urllib3 as u\n"
            "[u.Retry() for x in source for u in u.Retry()]\n",
            [Usage(Path("app.py"), 1, "urllib3", "import")],
        ),
        (
            "import urllib3 as u\n"
            "def build():\n"
            "    global u\n"
            "    u.Retry()\n"
            "    u = other\n",
            [
                Usage(Path("app.py"), 1, "urllib3", "import"),
                Usage(Path("app.py"), 4, "urllib3.Retry", "call"),
            ],
        ),
        (
            "def outer():\n"
            "    import urllib3 as u\n"
            "    def build():\n"
            "        nonlocal u\n"
            "        u.Retry()\n"
            "        u = other\n",
            [
                Usage(Path("app.py"), 2, "urllib3", "import"),
                Usage(Path("app.py"), 5, "urllib3.Retry", "call"),
            ],
        ),
    ],
    ids=[
        "function-parameter",
        "local-assignment-before-binding",
        "assignment-right-hand-side",
        "relative-import",
        "class-and-method-scopes",
        "comprehension-target",
        "nested-class",
        "later-comprehension-target",
        "global-binding",
        "nonlocal-binding",
    ],
)
def test_shadowed_aliases(tmp_path: Path, source: str, expected: list[Usage]) -> None:
    write_source(tmp_path, "app.py", source)

    assert scan(tmp_path, "urllib3") == expected


def test_skips_undecodable_python_file(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_bytes(b"import urllib3\n# \xff\n")
    write_source(tmp_path, "valid.py", "import urllib3\n")

    assert scan(tmp_path, "urllib3") == [
        Usage(Path("valid.py"), 1, "urllib3", "import")
    ]
