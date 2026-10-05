"""Security rules for action.yml, checked on its text (PyYAML is not a dependency)."""

from pathlib import Path
import re

import pytest

from upgrade_impact.comment import MARKER


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT = (PROJECT_ROOT / "action.yml").read_text(encoding="utf-8")
ACTION_SOURCE = (PROJECT_ROOT / "src" / "upgrade_impact" / "action.py").read_text(encoding="utf-8")

COMMENT = "Comment on the pull request"
SETUP = "Set up uv"
ANALYSIS = "Check the upgrades"


def steps() -> dict[str, str]:
    """Return the text of each step under runs.steps, by name."""
    body = TEXT.split("\n  steps:\n", 1)[1]
    blocks = re.split(r"\n(?=    - )", body)
    return {
        match.group(1): block
        for block in blocks
        if (match := re.match(r"\s*- name: (.+)", block))
    }


def script(step: str) -> str:
    """Return a step's run script, written inline or as a block."""
    block = re.search(r"^      run: \|\n((?:        .*\n|\n)*)", step + "\n", re.M)
    if block:
        return block.group(1)
    return re.search(r"^      run: (.+)$", step, re.M).group(1)


def test_lists_the_steps_in_order() -> None:
    assert list(steps()) == [
        SETUP, ANALYSIS, COMMENT, "Write the job summary", "Fail on breaking findings",
    ]


def test_only_the_comment_step_receives_the_token() -> None:
    tokens = [name for name, step in steps().items() if "github.token" in step]

    assert tokens == [COMMENT]
    assert TEXT.count("github.token") == 1
    assert "secrets." not in TEXT
    # No input can hand a different token to the action.
    inputs = TEXT.split("\ninputs:\n", 1)[1].split("\nruns:\n", 1)[0]
    assert not re.findall(r"^  [\w-]*token[\w-]*:", inputs, re.M)


@pytest.mark.parametrize("name", [SETUP, ANALYSIS])
def test_steps_that_run_downloaded_code_blank_the_token_variables(name: str) -> None:
    step = steps()[name]

    assert '        GITHUB_TOKEN: ""\n' in step
    assert '        GH_TOKEN: ""\n' in step


def test_setup_uv_gets_an_empty_token_and_a_verified_version() -> None:
    step = steps()[SETUP]

    assert '        github-token: ""\n' in step
    assert re.search(r'^        version: "\d+\.\d+\.\d+"$', step, re.M)


def test_actions_are_pinned_to_full_commit_shas() -> None:
    uses = re.findall(r"uses: (\S+)", TEXT)

    assert uses
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action) for action in uses)


def test_scripts_take_values_from_the_environment_not_expressions() -> None:
    # Expressions inside a script are pasted into the shell before it runs.
    for name, step in steps().items():
        if "run:" in step:
            assert "${{" not in script(step), name


def test_comments_only_on_pull_request_events() -> None:
    assert "if: github.event_name == 'pull_request' &&" in steps()[COMMENT]
    assert "if: github.event_name != 'pull_request' &&" in steps()["Write the job summary"]


def test_comment_step_updates_the_comment_marked_by_the_renderer() -> None:
    comment_script = script(steps()[COMMENT])

    assert f'startswith("{MARKER}")' in comment_script
    assert '.user.login == "github-actions[bot]"' in comment_script
    assert "--method PATCH" in comment_script
    assert "--method POST" in comment_script


def test_analysis_runs_the_cli_from_the_actions_own_checkout() -> None:
    step = steps()[ANALYSIS]

    assert "        ACTION_PATH: ${{ github.action_path }}\n" in step
    assert script(step) == 'uvx --from "$ACTION_PATH" python -m upgrade_impact.action'


def test_every_input_reaches_the_analysis_step() -> None:
    inputs = re.findall(r"^  ([\w-]+):$", TEXT.split("\nruns:\n", 1)[0], re.M)
    step = steps()[ANALYSIS]

    assert inputs == [
        "updated-dependencies-json", "package", "old-version", "new-version", "path",
        "fail-on-breaking",
    ]
    for name in inputs:
        variable = "INPUT_" + name.upper().replace("-", "_")
        assert f"        {variable}: ${{{{ inputs.{name} }}}}\n" in step
        assert f'"{variable}"' in ACTION_SOURCE
