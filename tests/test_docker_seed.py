"""Tests for the helper that seeds the qBittorrent configuration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "docker" / "seed_qbittorrent_config.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("seed_qbittorrent_config", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


seed_module = _load_module()


def test_password_hash_round_trip():
    stored = seed_module.hash_password("s3cret")
    assert seed_module.verify_password("s3cret", stored)
    assert not seed_module.verify_password("other", stored)


def test_password_hash_uses_the_qbittorrent_format():
    stored = seed_module.hash_password("s3cret")
    salt, key = stored.split(":")
    assert len(salt) == 24  # 16 bytes base64 encoded
    assert len(key) == 88  # 64 bytes base64 encoded


def test_encode_and_decode_value():
    encoded = seed_module.encode_value("salt:key")
    assert encoded == "@ByteArray(salt:key)"
    assert seed_module.decode_value(encoded) == "salt:key"


def test_decode_value_accepts_plain_values():
    assert seed_module.decode_value("salt:key") == "salt:key"
    assert seed_module.decode_value('"salt:key"') == "salt:key"


def test_decode_value_accepts_quoted_byte_arrays():
    """qBittorrent quotes values that contain an equal sign."""
    raw = '"@ByteArray(salt:key)"'
    assert seed_module.decode_value(raw) == "salt:key"


def test_the_stored_hash_can_be_verified(tmp_path):
    """The value in the ini file has to be readable by qBittorrent."""
    config = tmp_path / "qBittorrent.conf"
    seed_module.seed(config, "admin", "s3cret", "")

    text = config.read_text()
    stored = seed_module.read_value(text, "WebUI", "Password_PBKDF2")
    assert stored is not None

    # This is what QSettings hands over to qBittorrent: the raw bytes
    # between the parentheses, they have to contain the colon.
    secret = seed_module.decode_value(stored)
    assert secret.count(":") == 1
    assert seed_module.verify_password("s3cret", secret)


def test_set_value_replaces_an_existing_key():
    text = "[Preferences]\nWebUI\\Username=old\nOther=1\n"
    result = seed_module.set_value(text, "Preferences", "WebUI\\Username", "new")
    assert "WebUI\\Username=new" in result
    assert "old" not in result
    assert "Other=1" in result


def test_set_value_adds_a_missing_key():
    text = "[Preferences]\nOther=1\n"
    result = seed_module.set_value(text, "Preferences", "WebUI\\Username", "admin")
    assert "[Preferences]" in result
    assert "WebUI\\Username=admin" in result
    assert "Other=1" in result


def test_set_value_adds_a_missing_section():
    result = seed_module.set_value("[Other]\nx=1\n", "Preferences", "WebUI\\Username", "admin")
    assert "[Preferences]" in result
    assert "WebUI\\Username=admin" in result
    assert "[Other]" in result


def test_read_value():
    text = "[Preferences]\nWebUI\\Username=admin\n"
    assert seed_module.read_value(text, "Preferences", "WebUI\\Username") == "admin"
    assert seed_module.read_value(text, "Preferences", "WebUI\\Password_PBKDF2") is None


def test_seed_creates_a_configuration_file(tmp_path):
    config = tmp_path / "qBittorrent.conf"
    action = seed_module.seed(config, "admin", "s3cret", "172.28.0.0/16")

    assert action == "written"
    text = config.read_text()
    assert "WebUI\\Username=admin" in text
    assert "WebUI\\Address=*" in text
    assert "WebUI\\AuthSubnetWhitelistEnabled=true" in text
    assert "WebUI\\AuthSubnetWhitelist=172.28.0.0/16" in text

    stored = seed_module.read_value(text, "Preferences", "WebUI\\Password_PBKDF2")
    assert stored is not None
    assert seed_module.verify_password("s3cret", seed_module.decode_value(stored))


def test_seed_writes_both_config_layouts(tmp_path):
    """qBittorrent 5.x reads [WebUI], older releases read [Preferences]."""
    config = tmp_path / "qBittorrent.conf"
    seed_module.seed(config, "admin", "s3cret", "172.28.0.0/16")

    text = config.read_text()
    new = seed_module.read_value(text, "WebUI", "Password_PBKDF2")
    old = seed_module.read_value(text, "Preferences", "WebUI\\Password_PBKDF2")

    assert new is not None and old is not None
    assert seed_module.decode_value(new) == seed_module.decode_value(old)
    assert seed_module.verify_password("s3cret", seed_module.decode_value(new))

    assert seed_module.read_value(text, "WebUI", "Username") == "admin"
    assert seed_module.read_value(text, "WebUI", "Address") == "*"
    assert seed_module.read_value(text, "WebUI", "ServerDomains") == "*"
    assert seed_module.read_value(text, "WebUI", "CSRFProtection") == "false"
    assert seed_module.read_value(text, "WebUI", "AuthSubnetWhitelistEnabled") == "true"
    assert seed_module.read_value(text, "WebUI", "AuthSubnetWhitelist") == "172.28.0.0/16"


def test_seed_disables_the_host_header_validation(tmp_path):
    """The published port usually differs from the port in the container."""
    config = tmp_path / "qBittorrent.conf"
    seed_module.seed(config, "admin", "s3cret", "")

    text = config.read_text()
    assert seed_module.read_value(text, "WebUI", "HostHeaderValidation") == "false"
    assert "WebUI\\HostHeaderValidation=false" in text


def test_seed_finds_the_password_in_the_new_layout(tmp_path):
    """After the 5.x migration only the [WebUI] group is left over."""
    config = tmp_path / "qBittorrent.conf"
    seed_module.seed(config, "admin", "s3cret", "")
    text = config.read_text()

    # Simulate the migration: the old group is gone, the new one stays.
    text = seed_module.set_value(text, "Preferences", "WebUI\\Password_PBKDF2", "")
    config.write_text(text)

    action = seed_module.seed(config, "admin", "s3cret", "")
    assert action == "kept"


def test_seed_is_idempotent(tmp_path):
    config = tmp_path / "qBittorrent.conf"
    seed_module.seed(config, "admin", "s3cret", "")
    first = config.read_text()

    action = seed_module.seed(config, "admin", "s3cret", "")
    assert action == "kept"
    assert config.read_text() == first


def test_seed_rewrites_the_password_when_it_changes(tmp_path):
    config = tmp_path / "qBittorrent.conf"
    seed_module.seed(config, "admin", "s3cret", "")

    action = seed_module.seed(config, "admin", "other", "")
    assert action == "written"

    text = config.read_text()
    for section, key in (
        ("Preferences", "WebUI\\Password_PBKDF2"),
        ("WebUI", "Password_PBKDF2"),
    ):
        stored = seed_module.read_value(text, section, key)
        assert stored is not None
        assert seed_module.verify_password("other", seed_module.decode_value(stored))


def test_seed_can_disable_the_authentication_bypass(tmp_path):
    config = tmp_path / "qBittorrent.conf"
    seed_module.seed(config, "admin", "s3cret", "172.28.0.0/16")
    seed_module.seed(config, "admin", "s3cret", "")

    text = config.read_text()
    assert "WebUI\\AuthSubnetWhitelistEnabled=false" in text
    assert seed_module.read_value(text, "WebUI", "AuthSubnetWhitelistEnabled") == "false"


def test_seed_keeps_unrelated_settings(tmp_path):
    config = tmp_path / "qBittorrent.conf"
    config.write_text("[Preferences]\nConnection\\PortRangeMin=6881\n")

    seed_module.seed(config, "admin", "s3cret", "")

    assert "Connection\\PortRangeMin=6881" in config.read_text()


@pytest.mark.parametrize("password", ["", "with space", "ümläut", "a" * 200])
def test_any_password_can_be_hashed(password):
    stored = seed_module.hash_password(password)
    assert seed_module.verify_password(password, stored)
