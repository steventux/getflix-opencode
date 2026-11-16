"""Tests for the docker compose configuration.

The compose file is read as text on purpose, PyYAML is not a dependency of the
project and the values that are checked here are simple.
"""

from __future__ import annotations

import re
from pathlib import Path

_COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _compose() -> str:
    return _COMPOSE.read_text(encoding="utf-8")


def _value(name: str) -> str:
    match = re.search(rf"^\s+{name}:\s*(\S+)\s*$", _compose(), re.MULTILINE)
    assert match is not None, f"{name} is missing from docker-compose.yml"
    return match.group(1).strip()


def test_lan_network_has_a_non_empty_default():
    """The binhex image exits when LAN_NETWORK is empty, so the default counts."""
    value = _value("LAN_NETWORK")
    assert value != "${LAN_NETWORK:-}"
    assert re.fullmatch(r"\$\{LAN_NETWORK:-[0-9.]+/\d+\}", value), value


def test_the_killswitch_can_manage_the_firewall():
    """The VPN tunnel and the iptables rules need NET_ADMIN."""
    assert "cap_add:" in _compose()
    assert "- NET_ADMIN" in _compose()


def test_ports_are_bound_to_the_loopback_interface_by_default():
    text = _compose()
    assert "${QBITTORRENT_WEBUI_BIND:-127.0.0.1}" in text
    assert "${GETFLIX_BIND_ADDRESS:-127.0.0.1}" in text


def test_every_service_joins_the_same_network():
    """A service on its own network cannot resolve the others by name."""
    services_block = _compose().split("\nnetworks:\n", 1)[0]
    services = re.findall(r"^  ([a-z][a-z0-9-]*):$", services_block, re.MULTILINE)
    assert services == ["qbittorrent-init", "qbittorrent", "getflix"]
    # One `networks:` entry per service, the init container must be included.
    assert len(re.findall(r"^    networks:$", services_block, re.MULTILINE)) == 3


def test_the_subnet_is_pinned():
    """The subnet is also used for the qBittorrent authentication bypass."""
    assert "subnet: ${GETFLIX_DOCKER_SUBNET:-172.28.0.0/16}" in _compose()


def test_getflix_can_read_the_downloads_for_playback():
    """Completed downloads are streamed, so Getflix needs the files too."""
    services_block = _compose().split("\nnetworks:\n", 1)[0]
    getflix = services_block.split("\n  getflix:\n", 1)[1]
    assert "./downloads:/data:ro" in getflix
    assert "GETFLIX_DOWNLOAD_ROOT: ${GETFLIX_DOWNLOAD_ROOT:-/data}" in getflix
    assert "GETFLIX_VLC_URL_SCHEME: ${GETFLIX_VLC_URL_SCHEME:-true}" in getflix
    assert "GETFLIX_EXCLUDED_SITES: ${GETFLIX_EXCLUDED_SITES:-}" in getflix
