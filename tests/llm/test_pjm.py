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
]


@pytest.mark.parametrize("title,description,expected", CASES)
async def test_pjm_classifies(title, description, expected, tmp_path):
    prompt = render(*FilesystemPrompts(tmp_path).raw(PJM))
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
