"""Run one task through the graph. Usage: python scripts/run_task.py "title" "description" """
import sys
import uuid
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentic.graph import build  # noqa: E402


async def main() -> None:
    title = sys.argv[1] if len(sys.argv) > 1 else "Export report to CSV"
    description = sys.argv[2] if len(sys.argv) > 2 else (
        "Users ask for a way to take the report out of the product to work on it "
        "in a spreadsheet. Today the only option is copying the table by hand."
    )

    state = {
        "id": f"T-{uuid.uuid4().hex[:8]}",
        "title": title,
        "description": description,
        "sections": {},
        "cost_usd": 0.0,
    }

    final = await build().ainvoke(state)
    print(f"\nid:      {final['id']}")
    print(f"type:    {final.get('type')}")
    print(f"status:  {final.get('status', 'open')}")
    print(f"roles:   {', '.join(final.get('sections', {}))}")
    print(f"cost:    ${final.get('cost_usd', 0):.4f}")
    print(f"doc:     tasks/{final['id']}.md")


anyio.run(main)
