#!/usr/bin/env python3
"""Install the official qBittorrent search plugins.

qBittorrent 5.x ships without search engines, they can be installed from the
WebUI.  Without them the search API returns nothing and Getflix would never
find a torrent.

This script downloads the official plugin repository into the qBittorrent
profile, so that the plugins are available as soon as qBittorrent starts:

    <profile>/data/nova3/engines/<plugin>.py

The script never fails the container, a missing internet connection only
means that the plugins have to be installed from the WebUI later on.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

#: The directory that holds the official plugins.
PLUGIN_LIST_URL = (
    "https://api.github.com/repos/qbittorrent/search-plugins/contents/nova3/engines"
)

#: Where a single plugin is downloaded from.
PLUGIN_URL = (
    "https://raw.githubusercontent.com/qbittorrent/search-plugins/"
    "refs/heads/master/nova3/engines/{name}"
)

#: The plugins that are installed when the repository cannot be listed.
FALLBACK_PLUGINS = (
    "eztv",
    "jackett",
    "limetorrents",
    "piratebay",
    "solidtorrents",
    "torlock",
    "torrentproject",
    "torrentscsv",
)

TIMEOUT = 30


def _get(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "getflix-docker-init"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def list_plugins() -> list[str]:
    """Return the names of the available plugins."""
    try:
        payload = json.loads(_get(PLUGIN_LIST_URL).decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        print(f"[warn] Could not list the search plugins: {exc}", file=sys.stderr)
        return list(FALLBACK_PLUGINS)

    names: list[str] = []
    for entry in payload if isinstance(payload, list) else []:
        name = str(entry.get("name") or "")
        if name.endswith(".py") and name != "__init__.py":
            names.append(name[: -len(".py")])
    if not names:
        print("[warn] The plugin repository is empty, using the known plugins")
        return list(FALLBACK_PLUGINS)
    return sorted(names)


def install(target: Path, plugins: list[str]) -> list[str]:
    """Download the plugins into ``target`` and return the installed names."""
    target.mkdir(parents=True, exist_ok=True)

    # qBittorrent expects the package marker of the plugin folder.
    init_file = target / "__init__.py"
    if not init_file.exists():
        init_file.write_text("", encoding="utf-8")

    installed: list[str] = []
    for name in plugins:
        try:
            content = _get(PLUGIN_URL.format(name=f"{name}.py"))
        except (urllib.error.URLError, OSError) as exc:
            print(f"[warn] Could not download the plugin {name}: {exc}", file=sys.stderr)
            continue
        (target / f"{name}.py").write_bytes(content)
        installed.append(name)
    return installed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default=os.environ.get(
            "QBITTORRENT_SEARCH_PLUGINS", "/config/qBittorrent/data/nova3/engines"
        ),
        help="directory the plugins are installed in",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="install the plugins even when the folder is not empty",
    )
    parser.add_argument("--uid", type=int, default=int(os.environ.get("PUID", "1000")))
    parser.add_argument("--gid", type=int, default=int(os.environ.get("PGID", "1000")))
    args = parser.parse_args(argv)

    target = Path(args.target)
    existing = sorted(p.stem for p in target.glob("*.py") if p.stem != "__init__")
    if existing and not args.force:
        print(f"[info] {len(existing)} search plugins are already installed in {target}")
        return 0

    installed = install(target, list_plugins())
    if not installed:
        print(
            "[warn] No search plugins could be installed, install them from the "
            "qBittorrent WebUI instead",
            file=sys.stderr,
        )
        return 0

    for path in target.glob("*.py"):
        try:
            os.chown(path, args.uid, args.gid)
        except (PermissionError, OSError) as exc:  # pragma: no cover - depends on the fs
            print(f"[warn] Could not change the owner of {path}: {exc}", file=sys.stderr)

    print(f"[info] Installed {len(installed)} search plugins in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
