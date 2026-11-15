"""Tests for the helper that installs the qBittorrent search plugins."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "docker" / "install_search_plugins.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("install_search_plugins", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plugin_module = _load_module()


def _listing(*names: str) -> bytes:
    payload = [{"name": f"{name}.py", "type": "file"} for name in names]
    return json.dumps(payload).encode("utf-8")


def test_list_plugins_strips_the_suffix(monkeypatch):
    monkeypatch.setattr(plugin_module, "_get", lambda url: _listing("eztv", "jackett"))
    assert plugin_module.list_plugins() == ["eztv", "jackett"]


def test_list_plugins_ignores_the_package_marker(monkeypatch):
    monkeypatch.setattr(plugin_module, "_get", lambda url: _listing("__init__", "eztv"))
    assert plugin_module.list_plugins() == ["eztv"]


def test_list_plugins_falls_back_when_the_repository_is_unreachable(monkeypatch):
    def boom(url):
        raise OSError("no network")

    monkeypatch.setattr(plugin_module, "_get", boom)
    assert plugin_module.list_plugins() == list(plugin_module.FALLBACK_PLUGINS)


def test_list_plugins_falls_back_on_a_broken_response(monkeypatch):
    monkeypatch.setattr(plugin_module, "_get", lambda url: b"<html>nope</html>")
    assert plugin_module.list_plugins() == list(plugin_module.FALLBACK_PLUGINS)


def test_install_writes_the_plugins(monkeypatch, tmp_path):
    requested: list[str] = []

    def fake_get(url: str) -> bytes:
        requested.append(url)
        return b"# plugin\n"

    monkeypatch.setattr(plugin_module, "_get", fake_get)
    target = tmp_path / "engines"

    installed = plugin_module.install(target, ["eztv", "jackett"])

    assert installed == ["eztv", "jackett"]
    assert (target / "eztv.py").read_bytes() == b"# plugin\n"
    assert (target / "jackett.py").read_bytes() == b"# plugin\n"
    # qBittorrent expects the package marker in the plugin folder.
    assert (target / "__init__.py").exists()
    assert all(url.endswith(".py") for url in requested)


def test_install_skips_a_plugin_that_cannot_be_downloaded(monkeypatch, tmp_path):
    def fake_get(url: str) -> bytes:
        if "broken" in url:
            raise OSError("404")
        return b"# plugin\n"

    monkeypatch.setattr(plugin_module, "_get", fake_get)

    installed = plugin_module.install(tmp_path, ["good", "broken"])

    assert installed == ["good"]
    assert (tmp_path / "good.py").exists()
    assert not (tmp_path / "broken.py").exists()


def test_main_installs_the_plugins_into_an_empty_profile(tmp_path, monkeypatch):
    def fake_get(url: str) -> bytes:
        if url == plugin_module.PLUGIN_LIST_URL:
            return _listing("eztv")
        return b"# plugin\n"

    monkeypatch.setattr(plugin_module, "_get", fake_get)
    monkeypatch.setattr(plugin_module.os, "chown", lambda *args: None)

    target = tmp_path / "engines"
    exit_code = plugin_module.main(["--target", str(target)])

    assert exit_code == 0
    assert (target / "eztv.py").exists()


def test_main_keeps_already_installed_plugins(tmp_path, monkeypatch):
    target = tmp_path / "engines"
    target.mkdir()
    (target / "eztv.py").write_text("# plugin\n")

    def fail(url: str) -> bytes:
        raise AssertionError("the plugins should not be downloaded again")

    monkeypatch.setattr(plugin_module, "_get", fail)

    assert plugin_module.main(["--target", str(target)]) == 0


def test_main_survives_a_missing_network(tmp_path, monkeypatch):
    def boom(url: str) -> bytes:
        raise OSError("no network")

    monkeypatch.setattr(plugin_module, "_get", boom)
    monkeypatch.setattr(plugin_module.os, "chown", lambda *args: None)

    exit_code = plugin_module.main(["--target", str(tmp_path / "engines")])

    assert exit_code == 0


@pytest.mark.parametrize("name", plugin_module.FALLBACK_PLUGINS)
def test_every_fallback_plugin_is_a_valid_name(name):
    assert name.islower()
    assert " " not in name
