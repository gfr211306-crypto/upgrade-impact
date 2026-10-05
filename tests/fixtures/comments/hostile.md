<!-- upgrade-impact -->
### upgrade-impact

| Package | Upgrade | Result |
| --- | --- | --- |
| `evil\|pkg <b>bold</b>` | `1.0` → `` 2.0` `` | ❌ 1 breaking · 1 to review |
| `broken` | `1.0` → `2.0` | could not analyze\: Could not download wheel for broken\=\=1\.0\: ERROR\: No matching distribution \| see \[docs\]\(https\:\/\/evil\.example\) \@​octocat \<script\>alert\(1\)\<\/script\> \#​1 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx… |

<details>
<summary>2 findings</summary>

**`evil|pkg <b>bold</b>`** `1.0` → `` 2.0` ``

- ⚠️ [`src/[docs](https:/evil.example)/a b#1.py:3`](https://github.com/octo-org/octo-repo/blob/0123456789abcdef0123456789abcdef01234567/src/%5Bdocs%5D%28https%3A/evil.example%29/a%20b%231.py#L3) evil\.f\(\.\.\.\) relies on the default of `x`\, which changed from `None` to `'@octocat' | [click](https://evil.example)`
- ❌ [`@octocat.py:7`](https://github.com/octo-org/octo-repo/blob/0123456789abcdef0123456789abcdef01234567/%40octocat.py#L7) evil\.g\(\.\.\.\) may break\: \<img src\=x onerror\=alert\(1\)\> \@​octocat fixes \#​1 \` see https\:\/\/evil\.example \<\!\-\- upgrade\-impact \-\-\>

</details>
