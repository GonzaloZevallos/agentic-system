"""`poller_units` (GH-726): the one reader of a product dir out of a unit's text, and the unit
dir's XDG rule. Pure: the golden row renders the real template the way `install-poller.sh`
does, the others are what the parser must refuse instead of guess."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from agentic.core.process.domain.poller_units import product_dir, unit_dir
from agentic.core.process.domain.stage0_rotation import Unreadable

TEMPLATE = Path(__file__).parents[4] / "scripts" / "systemd" / "agentic-poll.service.in"


def service(*lines: str, header: str = "[Service]") -> str:
    return "\n".join(("[Unit]", "Description=x", "", header, *lines, "", "[Install]", ""))


def test_the_unit_install_poller_renders_reads_back_its_product_dir() -> None:
    rendered = (
        TEMPLATE.read_text()
        .replace("@STAGE0@", "/home/u/stage 0")
        .replace("@PRODUCT_DIR@", "/srv/my product")
        .replace("@PATH@", "/usr/bin:/bin")
    )

    assert product_dir(rendered) == Path("/srv/my product")


@pytest.mark.parametrize(
    "text",
    [
        service('ExecStart="/s/agentic" poll --product-dir "/srv/a"'),
        service("ExecStart=/s/agentic poll --product-dir /srv/a"),
        service("ExecStart=/s/agentic poll --product-dir=/srv/a"),
        service("ExecStart=/s/agentic poll --dry-run --product-dir /srv/a --once"),
        # an ExecStart outside [Service] is not the service's
        "[Unit]\nExecStart=/bin/false\n"
        + service("ExecStart=/s/agentic poll --product-dir /srv/a"),
        service("ExecStart=/s/agentic poll --product-dir /srv/a", header="[Service]")
        + "[Install]\nExecStart=/bin/false\n",
    ],
)
def test_the_grammar_install_poller_emits_is_read(text: str) -> None:
    assert product_dir(text) == Path("/srv/a")


@pytest.mark.parametrize(
    "execstart",
    [
        "/s/agentic poll --product-dir srv/a",  # relative
        '/s/agentic poll --product-dir ""',  # empty
        "/s/agentic poll --product-dir=./a",
        '/s/agentic poll --product-dir="/srv/a"',  # a quote inside a plain word
        "-/s/agentic poll --product-dir /srv/a",  # prefix characters
        "@/s/agentic poll --product-dir /srv/a",
        ":/s/agentic poll --product-dir /srv/a",
        "+/s/agentic poll --product-dir /srv/a",
        "!/s/agentic poll --product-dir /srv/a",
        "!!/s/agentic poll --product-dir /srv/a",
        "/s/agentic poll --product-dir /srv/a\\b",
        "/s/agentic poll --product-dir /srv/a \\",
        "/s/agentic poll --product-dir /srv/%h",
        "/s/agentic poll --product-dir $HOME/a",
        '/s/agentic poll --product-dir "/srv/a',  # unbalanced quote
        '/s/agentic poll --product-dir /srv/a"',
        '/s/agentic poll --product-dir "/srv/a"b',
        "/s/agentic poll --product-dir '/srv/a'",
        "/s/agentic poll",  # no --product-dir
        "/s/agentic poll --product-dir",  # no value
        "/s/agentic poll --product-dir /srv/a --product-dir /srv/b",
        "/s/agentic poll --product-dir /srv/a --product-dir=/srv/b",
        "/s/agentic poll --product-dir --product-dir",
        "/s/agentic --product-dir /srv/a",  # not a poll
        "/s/agentic --product-dir poll",
        "/s/agentic polling --product-dir /srv/a",
        "",
    ],
)
def test_anything_else_in_an_execstart_is_unreadable(execstart: str) -> None:
    assert product_dir(service(f"ExecStart={execstart}")) == Unreadable()


@pytest.mark.parametrize(
    "text",
    [
        service("Type=simple"),  # zero ExecStart
        "",
        service(
            "ExecStart=/s/a poll --product-dir /srv/a", "ExecStart=/s/a poll --product-dir /srv/a"
        ),
        # one under each of two [Service] headers: still two
        service("ExecStart=/s/a poll --product-dir /srv/a")
        + "[Service]\nExecStart=/s/a poll --product-dir /srv/b\n",
        # the only ExecStart is not under [Service]
        "[Unit]\nExecStart=/s/a poll --product-dir /srv/a\n[Service]\nType=simple\n",
        # a line continued by a backslash may swallow the next one
        service("Description=x \\", "ExecStart=/s/a poll --product-dir /srv/a"),
        "[Service]\nExecStart=/s/a poll --product-dir /srv/a\0\n",
    ],
)
def test_a_unit_without_exactly_one_service_execstart_is_unreadable(text: str) -> None:
    assert product_dir(text) == Unreadable()


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"XDG_CONFIG_HOME": "/x/cfg", "HOME": "/h"}, Path("/x/cfg/systemd/user")),
        ({"XDG_CONFIG_HOME": "/x/cfg"}, Path("/x/cfg/systemd/user")),
        ({"XDG_CONFIG_HOME": "", "HOME": "/h"}, Path("/h/.config/systemd/user")),
        ({"XDG_CONFIG_HOME": "rel/cfg", "HOME": "/h"}, Path("/h/.config/systemd/user")),
        ({"HOME": "/h"}, Path("/h/.config/systemd/user")),
        ({}, Unreadable()),
        ({"XDG_CONFIG_HOME": ""}, Unreadable()),
        ({"HOME": ""}, Unreadable()),
        ({"HOME": "rel"}, Unreadable()),
        ({"XDG_CONFIG_HOME": "rel", "HOME": "rel"}, Unreadable()),
    ],
)
def test_the_unit_dir_follows_the_xdg_rule(
    environ: Mapping[str, str], expected: Path | Unreadable
) -> None:
    assert unit_dir(environ) == expected
