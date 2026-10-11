"""Security rules for the README workflow example and the self-test workflow."""

from pathlib import Path
import re

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
EXAMPLE = re.search(
    r"Add `\.github/workflows/upgrade-impact\.yml`:\n\n```yaml\n(.*?)```", README, re.S
).group(1)
SELF_TEST = (PROJECT_ROOT / ".github" / "workflows" / "self-test.yml").read_text(encoding="utf-8")
ACTION = (PROJECT_ROOT / "action.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("workflow", [EXAMPLE, SELF_TEST], ids=["readme", "self-test"])
def test_workflows_follow_the_security_rules(workflow: str) -> None:
    assert re.search(r"^on: pull_request$", workflow, re.M)
    assert "pull_request_target" not in re.sub(r"#.*", "", workflow)
    assert re.search(
        r"^permissions:\n  contents: read\n  pull-requests: write\n(?!  )", workflow, re.M
    )
    # Workflows never hand a token to a step themselves.
    assert "secrets." not in workflow
    assert "github.token" not in workflow
    assert "persist-credentials: false" in workflow


def test_readme_example_runs_only_for_dependabot() -> None:
    assert "    if: github.event.pull_request.user.login == 'dependabot[bot]'\n" in EXAMPLE


def test_readme_example_runs_fetch_metadata_as_its_own_step() -> None:
    steps = re.findall(r"^      - (?:id: \w+\n        )?uses: (\S+)", EXAMPLE, re.M)

    assert [step.split("@")[0] for step in steps] == [
        "actions/checkout", "dependabot/fetch-metadata", "gfr211306-crypto/upgrade-impact",
    ]
    assert (
        "updated-dependencies-json: ${{ steps.metadata.outputs.updated-dependencies-json }}"
        in EXAMPLE
    )
    # fetch-metadata needs the token, so it must not run inside the action.
    assert "fetch-metadata" not in re.sub(r"#.*", "", ACTION.split("\nruns:\n", 1)[1])


def test_self_test_checks_the_demo_upgrade_with_manual_inputs() -> None:
    runs = SELF_TEST.split("uses: ./\n")[1:]

    assert len(runs) == 2
    for run in runs:
        assert "          package: MarkupSafe\n" in run
        assert "          old-version: 2.0.1\n" in run
        assert "          new-version: 2.1.0\n" in run
        assert "          path: tests/fixtures/demo\n" in run
    assert "fail-on-breaking: false" in runs[0]
    assert "fail-on-breaking" not in runs[1].split("\n      - ")[0]
    assert 'test "$OUTCOME" = failure' in SELF_TEST
