"""Node evals against a real model. Excluded from CI: `uv run pytest -m llm`."""

import pytest

from agentic.core.process.adapters.claude_sdk import ClaudeSdkRunner
from agentic.core.process.adapters.filesystem import FilesystemPrompts
from agentic.core.process.application.run_role import run_with_retry
from agentic.core.process.domain.roles import PJM, render

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

CASES = [
    (
        "Bulk delete of archived items",
        "Admins delete archived items one by one. They want to select several and delete "
        "them at once.",
        "feature",
    ),
    (
        "Attendance report shows yesterday's date",
        "The attendance report header prints the previous day's date after midnight. "
        "It used to be correct.",
        "bug",
    ),
    (
        "Move session store to Redis",
        "In-memory sessions are lost on every deploy. No behaviour change for users.",
        "platform_enhancement",
    ),
    (
        # gym-saas #20: the closing sentence is what made PjM escalate instead of
        # classifying (docs/product/classification.md). It stays a bug even though it
        # offers both resolutions.
        "last_check_in_day is present as null instead of omitted",
        "Defined: when a member has no check-in today, the payload omits "
        "`last_check_in_day`.\n"
        "Observed: the field is present with value `null`.\n"
        "How to reproduce: call the endpoint for a member with no check-in today.\n"
        "Either the key is omitted when there is no check-in, or the definition says "
        "`null`.",
        "bug",
    ),
]


@pytest.mark.parametrize("title,description,expected", CASES)
async def test_pjm_classifies(title, description, expected, tmp_path):
    # `pjm.md` names `{{product}}` (ADR-0019); every case here is shaped like a request
    # about a product with definitions and a PM, so a product name, not the system's.
    prompt = render(*FilesystemPrompts(tmp_path).raw(PJM), product="gym-saas")
    output = await run_with_retry(
        PJM,
        f"Task: {title}\n\n{description}",
        system_prompt=prompt.text,
        model="claude-sonnet-5",
        retries=1,
        vocabulary={},
        cwd=tmp_path,
        runner=ClaudeSdkRunner(),
    )
    assert output.data["type"] == expected
    assert output.data["confidence"] == "high"
    assert output.text
