<!-- upgrade-impact -->
### upgrade-impact

| Package | Upgrade | Result |
| --- | --- | --- |
| `MarkupSafe` | `2.0.1` → `2.1.0` | ❌ 2 breaking |
| `urllib3` | `1.26.15` → `2.0.0` | ❌ 2 breaking |
| `Jinja2` | `3.1.0` → `3.1.4` | ✅ no findings |
| `python-dateutil` | `2.8.2` → `2.9.0.post0` | not imported |
| `functools32` | `3.2.3-2` → `3.2.3-1` | could not analyze\: No wheel is available for functools32\=\=3\.2\.3\-2\; source distributions are disabled to avoid running build code |

<details>
<summary>4 findings</summary>

**`MarkupSafe`** `2.0.1` → `2.1.0`

- ❌ [`tests/fixtures/demo/app.py:1`](https://github.com/octo-org/octo-repo/blob/0123456789abcdef0123456789abcdef01234567/tests/fixtures/demo/app.py#L1) markupsafe\.soft\_unicode was removed
- ❌ [`tests/fixtures/demo/app.py:9`](https://github.com/octo-org/octo-repo/blob/0123456789abcdef0123456789abcdef01234567/tests/fixtures/demo/app.py#L9) markupsafe\.soft\_unicode was removed

**`urllib3`** `1.26.15` → `2.0.0`

- ❌ [`tests/fixtures/demo/app.py:6`](https://github.com/octo-org/octo-repo/blob/0123456789abcdef0123456789abcdef01234567/tests/fixtures/demo/app.py#L6) urllib3\.util\.retry\.Retry\(\.\.\.\) uses removed parameter `method_whitelist=`
- ❌ [`tests/fixtures/demo/app.py:7`](https://github.com/octo-org/octo-repo/blob/0123456789abcdef0123456789abcdef01234567/tests/fixtures/demo/app.py#L7) urllib3\.Retry\(\.\.\.\) uses removed parameter `method_whitelist=`

</details>
