"""Runtime configuration, read from the environment (or a ``.env`` file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _get_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def _get_int(name: str, default: int) -> int:
    raw = _get_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = _get_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get_str(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return default


def _get_list(name: str) -> tuple[str, ...]:
    raw = _get_str(name)
    if not raw:
        return ()
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Config:
    """Everything the application needs to know about its environment."""

    qbittorrent_url: str = "http://localhost:8080"
    #: The WebUI as the browser reaches it. Falls back to the URL above, which
    #: is only correct when Getflix and qBittorrent share a network namespace.
    qbittorrent_webui_url: str = ""
    qbittorrent_username: str = "admin"
    qbittorrent_password: str = "adminadmin"
    qbittorrent_verify_ssl: bool = True
    qbittorrent_timeout: float = 30.0

    search_timeout: float = 45.0
    max_results: int = 50
    min_score: float = 45.0

    seed_hours: int = 24
    save_path: str | None = None
    category: str | None = None
    tags: tuple[str, ...] = ("getflix",)
    categories: tuple[str, ...] = field(default_factory=tuple)

    #: Where the downloads are mounted, as Getflix sees them.
    download_root: str = "/data"
    #: The same directory as qBittorrent reports it (for host installations).
    qbittorrent_download_root: str = "/data"
    #: Link finished downloads with a vlc: URL so one click opens the player.
    vlc_url_scheme: bool = True

    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False

    @property
    def seeding_time_limit_minutes(self) -> int:
        """The share limit handed to qBittorrent, in minutes (1 day == 1440)."""
        return max(1, self.seed_hours) * 60

    @property
    def webui_url(self) -> str:
        """The qBittorrent WebUI, as the browser reaches it."""
        return (self.qbittorrent_webui_url or self.qbittorrent_url).rstrip("/")

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            qbittorrent_url=_get_str("QBITTORRENT_URL", "http://localhost:8080") or "",
            qbittorrent_webui_url=_get_str("GETFLIX_QBITTORRENT_WEBUI_URL", "") or "",
            qbittorrent_username=_get_str("QBITTORRENT_USERNAME", "admin") or "admin",
            qbittorrent_password=_get_str("QBITTORRENT_PASSWORD", "adminadmin") or "",
            qbittorrent_verify_ssl=_get_bool("QBITTORRENT_VERIFY_SSL", True),
            qbittorrent_timeout=_get_float("QBITTORRENT_TIMEOUT", 30.0),
            search_timeout=_get_float("GETFLIX_SEARCH_TIMEOUT", 45.0),
            max_results=_get_int("GETFLIX_MAX_RESULTS", 50),
            min_score=_get_float("GETFLIX_MIN_SCORE", 45.0),
            seed_hours=_get_int("GETFLIX_SEED_HOURS", 24),
            save_path=_get_str("GETFLIX_SAVE_PATH"),
            category=_get_str("GETFLIX_CATEGORY"),
            tags=_get_list("GETFLIX_TAGS") or ("getflix",),
            categories=_get_list("GETFLIX_CATEGORIES"),
            download_root=_get_str("GETFLIX_DOWNLOAD_ROOT", "/data") or "/data",
            qbittorrent_download_root=_get_str(
                "GETFLIX_QBITTORRENT_DOWNLOAD_ROOT", "/data"
            )
            or "/data",
            vlc_url_scheme=_get_bool("GETFLIX_VLC_URL_SCHEME", True),
            host=_get_str("GETFLIX_HOST", "127.0.0.1") or "127.0.0.1",
            port=_get_int("GETFLIX_PORT", 5000),
            debug=_get_bool("GETFLIX_DEBUG", False),
        )


def load_dotenv_if_present() -> None:
    """Load a ``.env`` file from the working directory, if there is one."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a hard dependency
        return
    load_dotenv(override=False)
