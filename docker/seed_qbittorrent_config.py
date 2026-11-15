#!/usr/bin/env python3
"""Seed the qBittorrent configuration with a known WebUI password.

The ``binhex/arch-qbittorrentvpn`` image lets qBittorrent generate a random
WebUI password on the first start.  That password is only printed once, it is
not stored and it changes with every restart, which makes it unusable for an
application that has to talk to the WebUI API.

This script writes the password hash into ``qBittorrent.conf`` before
qBittorrent is started for the first time.  It follows the format used by
qBittorrent itself:

* the salt is 16 random bytes,
* the key is derived with PBKDF2-HMAC-SHA512, 100000 iterations, 64 bytes,
* both are stored base64 encoded and separated by a colon,
* the whole string is stored as a ``QByteArray`` inside the ini file.
  ``QSettings`` writes the raw bytes of a byte array, the value is therefore
  stored as plain text and not base64 encoded a second time.

qBittorrent 5.x moved the WebUI settings from the ``[Preferences]`` group
(``WebUI/Password_PBKDF2``) into a dedicated ``[WebUI]`` group
(``Password_PBKDF2``).  The migration only happens once, on the first start of
a 5.x release, so afterwards only the new group is read.  The script
therefore writes both layouts, which keeps old and new qBittorrent releases
happy.

The script is idempotent: an existing password that already matches is kept,
so restarting the stack does not invalidate a running session.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import hashlib
import os
import re
import sys
from pathlib import Path

#: Number of PBKDF2 iterations, matches ``Utils::Password::PBKDF2``.
ITERATIONS = 100000

#: Length of the random salt in bytes, matches qBittorrent.
SALT_LENGTH = 16

#: Length of the derived key in bytes (SHA512), matches qBittorrent.
KEY_LENGTH = 64

#: The section used by qBittorrent 5.x and newer.
SECTION_NEW = "WebUI"

#: The section used by qBittorrent 4.x and older.
SECTION_OLD = "Preferences"

#: The prefix the 4.x layout uses for the WebUI keys.
PREFIX_OLD = "WebUI\\"

#: The configuration used by the binhex image, used when no configuration
#: file exists yet. The interface names are replaced by the container when
#: the VPN device is known.
DEFAULT_CONFIG = """[AutoRun]
enabled=false
program=

[BitTorrent]
Session\\Interface=wg0
Session\\InterfaceName=wg0

[Preferences]
General\\Locale=en
Connection\\PortRangeMin=6881
Connection\\UPnP=false
Connection\\Interface=wg0
Connection\\InterfaceName=wg0
General\\UseRandomPort=false

[WebUI]
Address=*
AuthSubnetWhitelist=
AuthSubnetWhitelistEnabled=false
CSRFProtection=false
ClickjackingProtection=false
HostHeaderValidation=false
LocalHostAuth=false
ServerDomains=*
Username=admin
"""

_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")
_BYTE_ARRAY_RE = re.compile(r"^@ByteArray\((?P<payload>.*)\)$")


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return the qBittorrent password hash for ``password``.

    The result looks like ``<base64 salt>:<base64 key>``.
    """
    if salt is None:
        salt = os.urandom(SALT_LENGTH)
    key = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, ITERATIONS, KEY_LENGTH)
    return f"{base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()}"


def verify_password(password: str, stored: str) -> bool:
    """Check ``password`` against a hash created by :func:`hash_password`."""
    parts = stored.split(":")
    if len(parts) != 2:
        return False
    try:
        salt = base64.b64decode(parts[0], validate=True)
        key = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(salt) != SALT_LENGTH or len(key) != KEY_LENGTH:
        return False
    return (
        hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, ITERATIONS, KEY_LENGTH) == key
    )


def encode_value(value: str) -> str:
    """Encode a value the way ``QSettings`` stores a ``QByteArray``.

    ``QSettings`` writes the raw bytes of the array between the parentheses,
    it does not base64 encode them.  The value therefore has to be stored
    as plain text, for example ``salt:key``.
    """
    return f"@ByteArray({value})"


def decode_value(raw: str) -> str:
    """Decode a value written by :func:`encode_value`.

    Plain values are returned unchanged, qBittorrent accepts both.
    """
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) > 1:
        raw = raw[1:-1]
    match = _BYTE_ARRAY_RE.match(raw)
    if match is None:
        return raw
    return match.group("payload")


# ---------------------------------------------------------------------------
# ini file handling
# ---------------------------------------------------------------------------
def read_value(text: str, section: str, key: str) -> str | None:
    """Return the value of ``key`` in ``section``, or ``None``."""
    current = ""
    for line in text.splitlines():
        match = _SECTION_RE.match(line)
        if match is not None:
            current = match.group("name").strip()
            continue
        if current != section:
            continue
        name, separator, value = line.partition("=")
        if separator and name.strip() == key:
            return value.strip()
    return None


def set_value(text: str, section: str, key: str, value: str) -> str:
    """Return ``text`` with ``key`` set to ``value`` inside ``section``."""
    lines = text.splitlines()
    section_start: int | None = None
    section_end: int | None = None
    key_index: int | None = None
    current = ""

    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if match is not None:
            if current == section:
                section_end = index
            current = match.group("name").strip()
            if current == section and section_start is None:
                section_start = index
            continue
        if current == section and key_index is None:
            name, separator, _ = line.partition("=")
            if separator and name.strip() == key:
                key_index = index

    new_line = f"{key}={value}"
    if key_index is not None:
        lines[key_index] = new_line
    elif section_start is not None:
        lines.insert(section_end if section_end is not None else len(lines), new_line)
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines.append(new_line)

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------
def _stored_hash(text: str) -> str | None:
    """Return the password hash qBittorrent would use, if there is one.

    qBittorrent 5.x reads the ``[WebUI]`` group, older releases read the
    ``[Preferences]`` group.
    """
    for section, key in (
        (SECTION_NEW, "Password_PBKDF2"),
        (SECTION_OLD, f"{PREFIX_OLD}Password_PBKDF2"),
    ):
        stored = read_value(text, section, key)
        if stored:
            decoded = decode_value(stored)
            if decoded:
                return decoded
    return None


def _webui_settings(username: str, auth_subnet: str) -> dict[str, str]:
    """The WebUI settings that are written to both config layouts."""
    return {
        "Username": username,
        # Listen on every interface, Getflix reaches the WebUI over the
        # docker network.
        "Address": "*",
        "ServerDomains": "*",
        # The API client does not send a CSRF token.
        "CSRFProtection": "false",
        # Do not let anybody on the host talk to the WebUI without a password.
        "LocalHostAuth": "false",
        # The published port usually differs from the port inside the
        # container, the host header check would reject those requests.
        "HostHeaderValidation": "false",
        "ClickjackingProtection": "false",
        # Requests coming from the compose network skip the authentication,
        # the WebUI stays password protected for everything else.
        "AuthSubnetWhitelistEnabled": "true" if auth_subnet else "false",
        "AuthSubnetWhitelist": auth_subnet,
    }


def seed(config_path: Path, username: str, password: str, auth_subnet: str) -> str:
    """Make sure the configuration contains the wanted WebUI credentials.

    Returns a short summary of what was done.
    """
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8", errors="replace")
    else:
        text = DEFAULT_CONFIG

    stored = _stored_hash(text)
    if stored is not None and verify_password(password, stored):
        # The password already matches, keep the existing hash.
        encoded = encode_value(stored)
        action = "kept"
    else:
        encoded = encode_value(hash_password(password))
        action = "written"

    settings = _webui_settings(username, auth_subnet)

    # qBittorrent 5.x reads the [WebUI] group, older releases the
    # [Preferences] group. Both are written so that the credentials work
    # with every supported release.
    for key, value in settings.items():
        text = set_value(text, SECTION_NEW, key, value)
    text = set_value(text, SECTION_NEW, "Password_PBKDF2", encoded)

    for key, value in settings.items():
        text = set_value(text, SECTION_OLD, f"{PREFIX_OLD}{key}", value)
    text = set_value(text, SECTION_OLD, f"{PREFIX_OLD}Password_PBKDF2", encoded)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return action


def chown(path: Path, uid: int, gid: int) -> None:
    """Change the owner of ``path``, ignore errors on exotic file systems."""
    try:
        os.chown(path, uid, gid)
    except (PermissionError, OSError) as exc:  # pragma: no cover - depends on the fs
        print(f"[warn] Could not change the owner of {path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=os.environ.get("QBITTORRENT_CONFIG_DIR", "/config/qBittorrent/config"),
        help="directory that holds qBittorrent.conf",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("QBITTORRENT_USERNAME", "admin"),
        help="WebUI user name",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("QBITTORRENT_PASSWORD", "adminadmin"),
        help="WebUI password",
    )
    parser.add_argument(
        "--auth-subnet",
        default=os.environ.get("QBITTORRENT_AUTH_SUBNET", ""),
        help="subnet that may talk to the WebUI without a password",
    )
    parser.add_argument(
        "--save-path",
        default=os.environ.get("GETFLIX_SAVE_PATH", ""),
        help="download directory that is created if it does not exist",
    )
    parser.add_argument("--uid", type=int, default=int(os.environ.get("PUID", "1000")))
    parser.add_argument("--gid", type=int, default=int(os.environ.get("PGID", "1000")))
    args = parser.parse_args(argv)

    config_path = Path(args.config) / "qBittorrent.conf"
    action = seed(config_path, args.username, args.password, args.auth_subnet)
    chown(config_path, args.uid, args.gid)
    with contextlib.suppress(OSError):  # depends on the file system
        config_path.chmod(0o664)
    print(f"[info] WebUI password for '{args.username}' {action} in {config_path}")

    if args.save_path:
        save_path = Path(args.save_path)
        save_path.mkdir(parents=True, exist_ok=True)
        chown(save_path, args.uid, args.gid)
        with contextlib.suppress(OSError):  # depends on the file system
            save_path.chmod(0o775)
        print(f"[info] Download directory {save_path} is ready")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
