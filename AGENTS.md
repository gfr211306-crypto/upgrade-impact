# AGENTS.md

## Project
`upgrade-impact` tells a Python project whether upgrading one dependency will break **its own code**.
It does not guess from version numbers. It compares the real API of both versions, then finds the
exact lines in this repo that use what changed.

```
$ upgrade-impact urllib3 1.26.15 2.0.0 ./my-repo
❌ app.py:6  urllib3.Retry(...) uses removed parameter `method_whitelist=`
1 breaking · 0 to review
```

## v0.1 scope (do not build more than this)
- Python only, CLI only.
- No GitHub Action, no LLM calls, no CVE scanning, no web UI. Write ideas in ROADMAP.md instead.
- Input: distribution name, old version, new version, repo path. Optional `--import-name`.
- Output: one line per finding (`file:line  message`), then one summary line.
- Exit code: 0 = no breaking findings, 1 = at least one ❌, 2 = tool error.
- All user-facing text in English.

## Layout
- `src/upgrade_impact/scan.py`: find usages in the user's repo
- `src/upgrade_impact/check.py`: compare versions and judge each usage
- `src/upgrade_impact/cli.py`: argument parsing, output, exit codes (entry point `upgrade-impact`)
- `tests/fixtures/demo/app.py`: acceptance fixture (below)

## Commands
- Setup: `uv sync`
- Run: `uv run upgrade-impact MarkupSafe 2.0.1 2.1.0 tests/fixtures/demo`
- Test: `uv run pytest` (tests download real packages from PyPI; griffe caches them)

## Approach (verified on real packages with griffe 2.3.0)
1. **Load both versions** with `griffe[pypi]`: `griffe.load_pypi(import_name, dist_name, "==1.26.15")`.
   - Gotcha: on a fresh machine this crashes with `FileNotFoundError`, because griffe's cache dir
     does not exist yet. Before the first call:
     `Path(platformdirs.user_cache_dir("griffe")).mkdir(parents=True, exist_ok=True)`
   - Default import name = dist name lowercased, `-` replaced by `_`. `--import-name` overrides it
     (example: PyYAML → yaml).
2. **Scan the repo** with the standard `ast` module.
   - Skip `.venv`, `venv`, `.git`, `node_modules`, `build`, `dist`, `site-packages`.
     Skip files that fail to parse.
   - Map local names to full dotted paths from `import a`, `import a.b as c`, `from a.b import x as y`
     (absolute imports only).
   - Record every import and every call that resolves into the package: file, line, dotted path,
     keyword argument names, number of positional arguments.
3. **Check 1, existence (the most important check).** Walk `.members` from the package root in the old
   and the new version. Present in old, missing in new → ❌. Missing in old too → skip the usage.
   - Why this matters: `griffe.find_breaking_changes` only covers what griffe treats as public API.
     It does not report MarkupSafe 2.1.0's removal of `soft_unicode` at all, and it reports Jinja2
     3.1.0's removal of `Markup` only as `jinja2.utils.Markup`, while real code imports `jinja2.Markup`.
4. **Check 2, signatures.** Index `griffe.find_breaking_changes(old, new)` by `breakage.obj.path`.
   For a call, resolve aliases with `obj.final_target.path`. For classes, also check `<path>.__init__`.
   - `PARAMETER_REMOVED` and the call passes that keyword → ❌
   - `PARAMETER_CHANGED_DEFAULT` and the call does not pass that argument → ⚠️
   - Any other breakage on an object the code calls → ⚠️
   - Ignore `ATTRIBUTE_CHANGED_VALUE`. It is mostly noise, such as `__version__`.
5. One finding per line. The highest severity wins.
6. Windows: call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at startup.
   Otherwise the emoji crash under cp950 when output is redirected to a file.

## Acceptance tests (real incidents)
Fixture `tests/fixtures/demo/app.py`:

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

| Upgrade | Expected |
|---|---|
| MarkupSafe 2.0.1 → 2.1.0 | ❌ lines 1 and 9 (`soft_unicode` removed), exit 1 |
| Jinja2 3.0.3 → 3.1.0 | ❌ lines 2 and 10 (`Markup` removed), exit 1 |
| urllib3 1.26.15 → 2.0.0 | ❌ lines 6 and 7 (`method_whitelist=` removed), exit 1 |
| Jinja2 3.1.0 → 3.1.4 | no findings, exit 0 |

## Build plan (week 1)
- Day 1: skeleton, this file, the fixture, and a failing acceptance test file.
- Day 2: `scan.py`, plus unit tests for import and alias mapping.
- Day 3: existence check. The MarkupSafe and Jinja2 3.0.3 → 3.1.0 tests pass.
- Day 4: signature check. The urllib3 test passes.
- Day 5: CLI, output format, exit codes, Windows UTF-8 fix.
- Day 6: GitHub Actions CI running `uv run pytest` on ubuntu-latest and windows-latest.
- Day 7: English README showing the three incidents, then release v0.1.0 to PyPI with Trusted Publishing.

## Rules for agents
- One small commit per change. Run `uv run pytest` before every commit. Never commit failing tests.
- Do not edit the acceptance tests to make them pass.
- Do not add dependencies besides `griffe[pypi]` and `pytest` without asking.
- If a task seems to need something outside v0.1 scope, stop and ask.
