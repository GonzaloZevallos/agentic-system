import io

from claude_agent_sdk import ResultMessage, StreamEvent

from agentic.core.process.adapters.claude_sdk import Printer


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


def done(**overrides) -> ResultMessage:
    fields = dict(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        total_cost_usd=0.5,
    )
    return ResultMessage(**{**fields, **overrides})


def test_printer_adds_no_line_with_no_deliveries_callback():
    # The default of every caller but the runner's own: byte-for-byte today's line.
    buffer = io.StringIO()
    Printer("PM", buffer)(done())
    assert buffer.getvalue() == "[PM] done: 1 turns, $0.5000\n"


def test_printer_adds_no_line_with_a_single_delivery_emission():
    buffer = io.StringIO()
    Printer("PM", buffer, deliveries=lambda: 1)(done())
    assert buffer.getvalue() == "[PM] done: 1 turns, $0.5000\n"


def test_printer_shows_the_count_when_there_was_more_than_one_delivery_emission():
    buffer = io.StringIO()
    Printer("PM", buffer, deliveries=lambda: 3)(done())
    assert buffer.getvalue() == (
        "[PM] done: 1 turns, $0.5000\n[PM] 3 delivery emissions this attempt\n"
    )
