import configparser
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
XML_TEMPLATE = ROOT / "deploy/systemd/loopback.xml.example"
DROP_IN_TEMPLATE = ROOT / "deploy/systemd/50-ai-cat-dds-loopback.conf.example"
ENVIRONMENT = '"CYCLONEDDS_URI=file:///etc/ai-cat-dds/loopback.xml"'


def parse_unit(text):
    unit = configparser.ConfigParser(interpolation=None)
    unit.optionxform = str
    unit.read_string(text)
    return unit


def test_cyclonedds_template_selects_only_loopback_for_any_domain():
    root = ET.parse(XML_TEMPLATE).getroot()
    assert root.tag == "CycloneDDS"
    assert root.find("Domain").attrib == {"Id": "any"}
    interfaces = root.findall("./Domain/General/Interfaces/NetworkInterface")
    assert len(interfaces) == 1
    assert interfaces[0].attrib == {"name": "lo"}


def test_cyclonedds_template_has_no_extra_network_or_hardware_settings():
    root = ET.parse(XML_TEMPLATE).getroot()
    assert [(node.tag, node.attrib) for node in root.iter()] == [
        ("CycloneDDS", {}),
        ("Domain", {"Id": "any"}),
        ("General", {}),
        ("Interfaces", {}),
        ("NetworkInterface", {"name": "lo"}),
    ]
    assert all(not (node.text or "").strip() for node in root.iter())


def test_drop_in_only_sets_the_process_cyclonedds_environment():
    text = DROP_IN_TEMPLATE.read_text(encoding="utf-8")
    assert [line for line in text.splitlines() if line.strip()] == [
        "[Service]", "Environment=" + ENVIRONMENT,
    ]
    unit = parse_unit(text)
    assert unit.sections() == ["Service"]
    assert dict(unit["Service"]) == {"Environment": ENVIRONMENT}


def test_drop_in_references_the_reviewed_deployment_file_path():
    value = parse_unit(DROP_IN_TEMPLATE.read_text(encoding="utf-8"))["Service"]["Environment"]
    name, uri = value.strip('"').split("=", 1)
    assert name == "CYCLONEDDS_URI"
    parsed = urlsplit(uri)
    assert parsed.scheme == "file"
    assert parsed.netloc == ""
    assert parsed.path == "/etc/ai-cat-dds/loopback.xml"
    assert not parsed.query and not parsed.fragment


@pytest.mark.parametrize("unit_name, executable", [
    ("toy_main.service", "toy_main"),
    ("toy_motor.service", "toy_control"),
    ("toy_ui.service", "toy_ui"),
    ("toy_light.service", "toy_light"),
    ("toy_voice.service", "toy_audio"),
])
def test_one_shared_drop_in_preserves_factory_commands_and_start_policy(unit_name, executable):
    # In-memory merge only: do not copy/install templates or invoke systemd.
    baseline = (
        "[Unit]\nDescription=Factory " + unit_name + "\n"
        "[Service]\nExecStart=/usr/bin/" + executable + "\nRestart=always\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    unit = parse_unit(baseline)
    before = {section: dict(unit[section]) for section in unit.sections()}
    unit.read_string(DROP_IN_TEMPLATE.read_text(encoding="utf-8"))
    assert dict(unit["Unit"]) == before["Unit"]
    assert dict(unit["Install"]) == before["Install"]
    assert dict(unit["Service"]) == {**before["Service"], "Environment": ENVIRONMENT}
