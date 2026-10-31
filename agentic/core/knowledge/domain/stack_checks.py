"""The keys of `stack.yaml` that speak of a check's time and readiness, and the one function that
checks the names they use (ADR-0065, addendum GH-451).

`check_budgets` (GH-365), `check_timeouts`, `not_ready_exit` and `verifications` live here, apart
from `stack.py`, which reads the file and hands each parser the pieces it needs: the declared
value, the names that exist and whether `delivery_checks` is declared. The parsers are pure and
know nothing of the file's other keys, so this module imports nothing of `stack.py`. Every key
that names a check or a verification validates it with `require_declared`, so the refusal reads
the same wherever it comes from."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agentic.core.process.domain.run_check import Verification

# Every sub-key one `verifications` entry declares.
VERIFICATION_KEYS = ("command", "wall_s")
# The exit codes `not_ready_exit` may name: a process cannot exit with anything else.
EXIT_CODE_MIN = 1
EXIT_CODE_MAX = 255
# What a key that is declared but empty is told, after its source and its key (ADR-0046).
EMPTY_DECLARATION = "is declared empty; leave the key out to declare none"


def require_declared(name: object, key: str, known: Iterable[str], source: object) -> None:
    """Refuse a `name` under `key` that is not among the declared `known` names, the one
    wording every key that names a check or a verification shares."""
    names = list(known)
    if name not in names:
        raise ValueError(
            f"{source}: {name!r} under `{key}` is not a check declared under `checks`; "
            f"expected one of {', '.join(names)}"
        )


def finite_positive(value: object) -> float | None:
    """`value` as seconds when it is a finite positive int or float (never a bool)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:  # an int too big for a float
        return None
    return number if 0 < number < float("inf") else None


def parse_check_budgets(
    found: object, source: object, known: Iterable[str], tiered: bool
) -> dict[str, float]:
    """Check name -> the seconds it may take on the `full_checks` run (GH-365). `found` is the
    declared value, non-empty by now; `tiered` is whether `delivery_checks` is declared, which
    the key requires: the budget is compared only on the once-per-task full run that tiers
    create."""
    if not isinstance(found, dict):
        raise ValueError(f"{source}: expected `check_budgets`, a mapping of checks to seconds")
    if not tiered:
        raise ValueError(
            f"{source}: `check_budgets` requires `delivery_checks`: a budget is compared only "
            "on the once-per-task full run on the integrated head that tiers create"
        )
    budgets: dict[str, float] = {}
    for name, seconds in found.items():
        require_declared(name, "check_budgets", known, source)
        if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds <= 0:
            raise ValueError(
                f"{source}: {name!r} under `check_budgets` expects a positive number of seconds"
            )
        budgets[str(name)] = seconds
    return budgets


def parse_check_timeouts(found: object, source: object, known: Iterable[str]) -> dict[str, float]:
    """Check name -> the wall, in seconds, the product gives that check. A verification carries
    its own `wall_s`, so only checks are named here."""
    if not isinstance(found, dict):
        raise ValueError(f"{source}: expected `check_timeouts`, a mapping of checks to seconds")
    walls: dict[str, float] = {}
    for name, seconds in found.items():
        require_declared(name, "check_timeouts", known, source)
        wall = finite_positive(seconds)
        if wall is None:
            raise ValueError(
                f"{source}: {name!r} under `check_timeouts` expects a positive number of seconds"
            )
        walls[str(name)] = wall
    return walls


def parse_not_ready_exit(found: object, source: object, known: Iterable[str]) -> dict[str, int]:
    """Check or verification name -> the exit code that means "the environment was not ready"
    (the convention is 75, EX_TEMPFAIL)."""
    if not isinstance(found, dict):
        raise ValueError(f"{source}: expected `not_ready_exit`, a mapping of names to exit codes")
    codes: dict[str, int] = {}
    for name, code in found.items():
        require_declared(name, "not_ready_exit", known, source)
        if (
            isinstance(code, bool)
            or not isinstance(code, int)
            or not EXIT_CODE_MIN <= code <= EXIT_CODE_MAX
        ):
            raise ValueError(
                f"{source}: {name!r} under `not_ready_exit` expects an exit code from "
                f"{EXIT_CODE_MIN} to {EXIT_CODE_MAX}"
            )
        codes[str(name)] = code
    return codes


def parse_verifications(
    found: object, source: object, checks: Mapping[str, str]
) -> dict[str, Verification]:
    """Verification name -> `{command, wall_s}`, in the file's order. A name that is also a
    declared check is refused: `run_check` takes one name and must know which it means."""
    if not isinstance(found, dict):
        raise ValueError(f"{source}: expected `verifications`, a mapping of verifications")
    parsed: dict[str, Verification] = {}
    for name, body in found.items():
        where = f"{source}: {name!r} under `verifications`"
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{source}: expected a non-empty name under `verifications`")
        if name in checks:
            raise ValueError(f"{where} collides with a check of the same name under `checks`")
        if not isinstance(body, dict):
            raise ValueError(f"{where} is not a mapping of {VERIFICATION_KEYS}")
        if unknown := [key for key in body if key not in VERIFICATION_KEYS]:
            raise ValueError(
                f"{where} has unknown key(s) {', '.join(sorted(map(str, unknown)))}; "
                f"expected {VERIFICATION_KEYS}"
            )
        command = body.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{where}: expected a non-empty `command`")
        if "wall_s" not in body:
            raise ValueError(f"{where}: expected `wall_s`, the seconds the verification may take")
        wall = finite_positive(body["wall_s"])
        if wall is None:
            raise ValueError(f"{where}: `wall_s` expects a positive number of seconds")
        parsed[name] = Verification(command=command.strip(), wall_s=wall)
    return parsed
