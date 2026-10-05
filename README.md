# upgrade-impact

Will upgrading one dependency break **your** code?

`upgrade-impact` does not guess from version numbers. It downloads both versions from PyPI,
compares their real APIs, and points at the exact lines in your repository that use what changed.

```console
$ upgrade-impact urllib3 1.26.15 2.0.0 ./my-repo
❌ app.py:6  urllib3.util.retry.Retry(...) uses removed parameter `method_whitelist=`
❌ app.py:7  urllib3.Retry(...) uses removed parameter `method_whitelist=`
2 breaking · 0 to review
```

## Install

```console
$ pipx install upgrade-impact
```

or `uv tool install upgrade-impact`, or `pip install upgrade-impact`. Requires Python 3.10 or newer.

## Usage

```console
$ upgrade-impact DISTRIBUTION OLD_VERSION NEW_VERSION REPO_PATH [--import-name NAME] [--format text|json]
```

- `DISTRIBUTION`: the name on PyPI, such as `urllib3` or `PyYAML`.
- `OLD_VERSION`, `NEW_VERSION`: exact versions, such as `1.26.15`, not ranges such as `>=2.0`.
- `REPO_PATH`: the directory to scan.
- Import names are detected from the downloaded wheel's `top_level.txt`, or its `RECORD` when
  that metadata is absent. For example, `python-dateutil` → `dateutil`, `PyYAML` → `yaml`, and
  `beautifulsoup4` → `bs4` work without an override.
- A distribution can provide several import names. Only those imported by your repository
  are analyzed. If none are imported, the tool prints `not imported` and exits 0.
- `--import-name`: optionally override detection with a specific import name.
- `--format`: `text` (default) or `json` for scripts and CI.

### Output

One line per finding, then a summary line:

- `❌ file:line  message`: the upgrade breaks this line.
- `⚠️ file:line  message`: the API this line uses changed; review it.

With `--format json`, stdout contains one JSON object, including when analysis fails:

```console
$ upgrade-impact MarkupSafe 2.0.1 2.1.0 tests/fixtures/demo --format json
```

```json
{
  "package": "MarkupSafe",
  "old": "2.0.1",
  "new": "2.1.0",
  "findings": [
    {"file": "app.py", "line": 1, "severity": "breaking", "message": "markupsafe.soft_unicode was removed"},
    {"file": "app.py", "line": 9, "severity": "breaking", "message": "markupsafe.soft_unicode was removed"}
  ],
  "summary": {"breaking": 2, "review": 0, "status": "ok"},
  "error": null
}
```

Finding severity is `breaking` or `review`. Summary status is `ok` when analysis completed,
`not imported` when the repository uses none of the distribution's imports, or `error` on a tool
error. On an error, `error` contains the reason and `findings` is empty. Exit codes are the same
for both formats.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No breaking findings |
| 1 | At least one ❌ |
| 2 | Tool error, such as invalid arguments or a version that does not exist on PyPI |

Exit code 1 lets a script or CI job stop an upgrade that would break the build.

## Three real incidents

Each of these upgrades broke real projects. The examples run against
[`tests/fixtures/demo/app.py`](tests/fixtures/demo/app.py):

```python
from markupsafe import soft_unicode
from jinja2 import Markup, Environment
import urllib3
from urllib3.util.retry import Retry

retry = Retry(total=3, method_whitelist=["GET"])
pool = urllib3.PoolManager(retries=urllib3.Retry(3, method_whitelist=["GET"]))
env = Environment()
s = soft_unicode("x")
m = Markup("<b>hi</b>")
```

### MarkupSafe 2.1.0 removed `soft_unicode`

This removal broke Jinja2 2.x installs across the ecosystem in February 2022.

```console
$ upgrade-impact MarkupSafe 2.0.1 2.1.0 tests/fixtures/demo
❌ app.py:1  markupsafe.soft_unicode was removed
❌ app.py:9  markupsafe.soft_unicode was removed
2 breaking · 0 to review
```

### Jinja2 3.1.0 removed `jinja2.Markup`

The class now lives only in MarkupSafe. A generic API diff reports this removal only as
`jinja2.utils.Markup`, while real code imports `jinja2.Markup`. `upgrade-impact` checks the path
your code actually uses.

```console
$ upgrade-impact Jinja2 3.0.3 3.1.0 tests/fixtures/demo
❌ app.py:2  jinja2.Markup was removed
❌ app.py:10  jinja2.Markup was removed
2 breaking · 0 to review
```

### urllib3 2.0.0 removed `Retry(method_whitelist=...)`

The import still works, so this break only shows up at call time. `upgrade-impact` compares
signatures and flags only the calls that pass the removed keyword.

```console
$ upgrade-impact urllib3 1.26.15 2.0.0 tests/fixtures/demo
❌ app.py:6  urllib3.util.retry.Retry(...) uses removed parameter `method_whitelist=`
❌ app.py:7  urllib3.Retry(...) uses removed parameter `method_whitelist=`
2 breaking · 0 to review
```

A compatible upgrade reports nothing and exits 0:

```console
$ upgrade-impact Jinja2 3.1.0 3.1.4 tests/fixtures/demo
0 breaking · 0 to review
```

## How it works

1. **Download both versions' wheels** from PyPI with `PIP_ONLY_BINARY=:all:`. Source distributions
   are rejected, so package build code never runs. [griffe](https://mkdocstrings.github.io/griffe/)
   reads the extracted Python sources with dynamic inspection disabled. Since analysis is static,
   a historical wheel can be read even if it targets another Python version or platform.
2. **Scan your repository** with Python's `ast` module. Imports such as `import a.b as c` and
   `from a.b import x as y` are mapped to full dotted paths, so aliased calls are found too.
   `.venv`, `venv`, `.git`, `node_modules`, `build`, `dist` and `site-packages` are skipped.
3. **Existence check:** a name your code uses that exists in the old version but not in the new
   one is ❌.
4. **Signature check:** for each call, including a class's `__init__`:
   - A removed parameter that you pass by keyword is ❌.
   - A removed parameter that you pass by position is ⚠️.
   - A changed default that your call relies on is ⚠️.
   - A moved parameter that you pass by position is ⚠️.
   - Any other breaking change to a function or class you call is ⚠️.

Each line gets one finding, with the most severe winning.

## Limitations

- Static analysis only. Names bound dynamically, star imports and relative imports are not
  followed.
- Only the package's own API is compared. Behavior changes that keep the same signature are not
  detected.
- Both versions must have wheels on PyPI. Source-only releases exit 2 with a clear error.
- Compiled modules without Python source or stubs cannot be inspected safely and produce a
  tool error when selected for analysis.

## Development

```console
$ uv sync
$ uv run pytest
```

The tests download real packages from PyPI.

## License

MIT. See [LICENSE](LICENSE).
