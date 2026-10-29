"""The PM interviewee decides from the owner's decisions without escalating (GH-431).
Manual, outside CI: `uv run pytest -m llm tests/llm/test_owner_decisions.py`."""

from pathlib import Path

import pytest

from agentic.core.knowledge.domain import knowledge, knowledge_index
from agentic.core.process.adapters.claude_sdk import ClaudeSdkRunner
from agentic.core.process.adapters.filesystem import FilesystemPrompts
from agentic.core.process.adapters.shell import ShellReaper
from agentic.core.process.application.run_role import run_with_retry
from agentic.core.process.domain.roles import PM_INTERVIEWEE, render

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

REPO = Path(__file__).resolve().parents[2]
QUESTION = (
    "Task: Rename the export button\n\nThe export button on the reports screen should say "
    "'Download'. The estimate is 38 USD.\n\n## Questions\n\n1. May this task be split, "
    "given that its estimate is 38 USD?"
)


async def test_the_pm_decides_a_split_question_the_seed_covers_without_escalating(tmp_path):
    seed = (REPO / knowledge.OWNER_DECISIONS[1]).read_text(encoding="utf-8")
    built = knowledge_index.build_index(
        {}, {}, {}, None, None, owner={knowledge.OWNER_DECISIONS[1]: seed}
    )
    raw, source = FilesystemPrompts(tmp_path).raw(PM_INTERVIEWEE)
    prompt = render(
        raw,
        source,
        max_changes=2,
        max_turns=PM_INTERVIEWEE.max_turns,
        flags="- none",
    )
    output = await run_with_retry(
        PM_INTERVIEWEE,
        f"{built.text}\n\n{QUESTION}",
        system_prompt=prompt.text,
        model="claude-sonnet-5",
        retries=1,
        vocabulary={},
        cwd=tmp_path,
        runner=ClaudeSdkRunner(
            reaper=ShellReaper("owner-test", Path("/nonexistent/worktrees")),
            product_key="owner-test",
        ),
        owner_task="GH-1",
    )
    assert output.data["questions_for_human"] in ("", [], [""])
    assert "D-" in output.text
