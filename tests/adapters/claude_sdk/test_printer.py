import io

from claude_agent_sdk import StreamEvent

from agentic.adapters.claude_sdk import Printer


def test_printer_skips_empty_thinking():
    buffer = io.StringIO()
    printer = Printer("PM", buffer)
    for event in (
        {"type": "content_block_start", "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": ""}},
        {"type": "content_block_delta", "delta": {"type": "signature_delta", "signature": "x"}},
        {"type": "content_block_stop"},
    ):
        printer(StreamEvent(uuid="u", session_id="s", event=event))
    assert buffer.getvalue() == ""
