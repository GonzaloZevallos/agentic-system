"""The spec route is off in production (GH-594, part 4b-1b): the real composition root builds
no spec reader and no spec validator, so a `Spec:` line is not looked at. Part 4b-4 inverts this
test when it wires them."""

from agentic.composition import default_ports
from agentic.core.process.domain import spec_delivery
from agentic.shared.configuration import Configuration


def test_the_real_composition_root_builds_no_spec_ports(tmp_path):
    ports = default_ports(Configuration(product_dir=tmp_path))

    assert ports.spec_reader is None
    assert ports.spec_validator is None
    assert spec_delivery.honoured(ports.spec_reader, ports.spec_validator) is False
