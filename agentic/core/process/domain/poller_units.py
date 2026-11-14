"""What the stage0 smoke reads from the user's `agentic-poll*.service` units (GH-726): the
product dir a unit's `ExecStart=` names, and the directory the units live in. Pure: the text
and the environment come as parameters.

`product_dir` accepts only the grammar `scripts/install-poller.sh` emits (an absolute
`ExecStart=` with `poll` and one `--product-dir`) and says `Unreadable` to anything else:
a unit it does not understand must keep the rotation, never be guessed at. Drop-ins
(`*.service.d/`) are not read: a `--product-dir` set there is invisible to it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .stage0_rotation import Unreadable

_PREFIXES = "-@:+!"
_FLAG = "--product-dir"


def product_dir(unit_text: str) -> Path | Unreadable:
    """The absolute product dir of the unit's single `ExecStart=` under `[Service]`, else
    `Unreadable`: a line continued by a backslash, zero or two `ExecStart=` lines (several
    `[Service]` headers count together), an `ExecStart=` prefix character, a backslash, `%` or
    `$` in the value, an unbalanced quote, a missing or repeated `--product-dir`, a relative
    path, or no `poll` word."""
    if "\0" in unit_text:
        return Unreadable()
    values: list[str] = []
    section = ""
    for raw in unit_text.splitlines():
        line = raw.strip()
        if raw.endswith("\\"):
            return Unreadable()  # a continuation may swallow the next line: not understood
        if line.startswith("[") and line.endswith("]"):
            section = line
        elif section == "[Service]" and line.startswith("ExecStart="):
            values.append(line.removeprefix("ExecStart=").strip())
    if len(values) != 1:
        return Unreadable()
    words = _words(values[0])
    return _product_dir_of(words) if words is not None else Unreadable()


def _words(value: str) -> list[str] | None:
    """The words of an `ExecStart=` value, or None when it carries anything but plain words
    and `"..."` words, or starts with a prefix character."""
    if not value or value[0] in _PREFIXES or any(c in value for c in "\\%$"):
        return None
    words: list[str] = []
    i = 0
    while i < len(value):
        if value[i].isspace():
            i += 1
        elif value[i] == '"':
            end = value.find('"', i + 1)
            if end == -1 or (end + 1 < len(value) and not value[end + 1].isspace()):
                return None
            words.append(value[i + 1 : end])
            i = end + 1
        else:
            end = i
            while end < len(value) and not value[end].isspace():
                if value[end] in "\"'":
                    return None
                end += 1
            words.append(value[i:end])
            i = end
    return words


def _product_dir_of(words: list[str]) -> Path | Unreadable:
    flags = [i for i, w in enumerate(words) if w == _FLAG or w.startswith(_FLAG + "=")]
    if len(flags) != 1:
        return Unreadable()
    at = flags[0]
    if words[at] == _FLAG:
        if at + 1 >= len(words):
            return Unreadable()
        value, taken = words[at + 1], {at, at + 1}
    else:
        value, taken = words[at].removeprefix(_FLAG + "="), {at}
    rest = [w for i, w in enumerate(words) if i not in taken]
    if "poll" not in rest or not value.startswith("/"):
        return Unreadable()
    return Path(value)


def unit_dir(environ: Mapping[str, str]) -> Path | Unreadable:
    """`$XDG_CONFIG_HOME/systemd/user` when `XDG_CONFIG_HOME` is non-empty and absolute, else
    `$HOME/.config/systemd/user`; `Unreadable` when that needs a `HOME` that is missing or
    not absolute."""
    xdg = environ.get("XDG_CONFIG_HOME", "")
    if xdg.startswith("/"):
        return Path(xdg) / "systemd" / "user"
    home = environ.get("HOME", "")
    if home.startswith("/"):
        return Path(home) / ".config" / "systemd" / "user"
    return Unreadable()
