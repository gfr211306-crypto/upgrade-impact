<!-- upgrade-impact -->
### upgrade-impact

| Package | Upgrade | Result |
| --- | --- | --- |
| `urllib3` | `1.26.15` → `2.0.0` | ⚠️ 1 to review |

<details>
<summary>1 finding</summary>

**`urllib3`** `1.26.15` → `2.0.0`

- ⚠️ [`client.py:3`](https://github.com/octo-org/octo-repo/blob/0123456789abcdef0123456789abcdef01234567/client.py#L3) urllib3\.util\.retry\.Retry\(\.\.\.\) relies on the default of `allowed_methods`\, which changed from `_Default` to `DEFAULT_ALLOWED_METHODS`

</details>
