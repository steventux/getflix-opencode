"""Small formatting helpers shared by the templates."""

from __future__ import annotations

from urllib.parse import urlparse

__all__ = [
    "human_size",
    "human_speed",
    "human_eta",
    "host_from_url",
    "human_duration",
    "state_label",
]

#: Human readable names for the qBittorrent torrent states.
STATE_LABELS: dict[str, str] = {
    "downloading": "Downloading",
    "forceddl": "Downloading",
    "metadl": "Fetching metadata",
    "forcedmetadl": "Fetching metadata",
    "stalledmetadl": "Fetching metadata",
    "stalleddl": "Stalled",
    "queueddl": "Queued",
    "checkingdl": "Checking files",
    "allocating": "Allocating space",
    "moving": "Moving files",
    "uploading": "Seeding",
    "forcedup": "Seeding",
    "stalledup": "Seeding",
    "pausedup": "Paused",
    "pauseddl": "Paused",
    "queuedup": "Queued",
    "checkingup": "Checking files",
    "error": "Error",
    "unknown": "Unknown",
    "paused": "Paused",
    "stoppedup": "Stopped",
    "stoppeddl": "Stopped",
}


def state_label(state: str | None) -> str:
    """``stalledUP`` -> ``Seeding``."""
    if not state:
        return "Unknown"
    key = state.lower()
    if key in STATE_LABELS:
        return STATE_LABELS[key]
    return state.replace("_", " ").title() or "Unknown"

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def _scale(value: float, unit: str) -> str:
    value = float(value)
    for index, suffix in enumerate(_UNITS):
        unit_name = suffix if unit == "size" else suffix + "/s"
        if value < 1024 or index == len(_UNITS) - 1:
            if index == 0:
                return f"{value:.0f} {unit_name}"
            return f"{value:.1f} {unit_name}"
        value /= 1024
    return f"{value:.1f} {unit}"


def human_size(num_bytes: float | int | None) -> str:
    """``1536`` -> ``1.5 KB``."""
    if not num_bytes:
        return "0 B"
    return _scale(num_bytes, "size")


def human_speed(bytes_per_second: float | int | None) -> str:
    """``1048576`` -> ``1.0 MB/s``."""
    if not bytes_per_second:
        return "0 B/s"
    return _scale(bytes_per_second, "speed")


def human_duration(seconds: float | int | None) -> str:
    """``93784`` -> ``1d 2h``."""
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def human_eta(seconds: float | int | None) -> str:
    """ETA in a compact, human readable form. ``8640000`` means "infinity"."""
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    if seconds >= 8640000:
        return "∞"
    return human_duration(seconds)


def host_from_url(url: str | None) -> str:
    """``https://example.com/path`` -> ``example.com``."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.netloc:
        return parsed.netloc
    return url
