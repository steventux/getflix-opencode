"""A small, dependency free client for the qBittorrent WebUI API.

Supports both the legacy (qBittorrent 4.x) and the current (qBittorrent 5.x)
search endpoints, and transparently re-authenticates when the WebUI session
expires.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

__all__ = [
    "QBittorrentClient",
    "QBittorrentError",
    "QBittorrentAuthError",
    "QBittorrentUnavailable",
    "SearchHit",
    "Torrent",
]


class QBittorrentError(RuntimeError):
    """The WebUI answered with an unexpected status code."""


class QBittorrentAuthError(QBittorrentError):
    """The supplied credentials were rejected."""


class QBittorrentUnavailable(QBittorrentError):
    """The WebUI could not be reached at all."""


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SearchHit:
    """A single result returned by a qBittorrent search plugin."""

    file_name: str
    file_url: str
    download_url: str
    details_url: str = ""
    site_url: str = ""
    file_size: int = 0
    seeders: int = 0
    leechers: int = 0
    category: str = ""
    channel: str = ""
    subcategory: str = ""

    @property
    def id(self) -> str:
        """A stable identifier for this result, used to track its download."""
        seed = self.download_url or self.file_url or self.file_name
        return hashlib.sha1(seed.encode("utf-8", "replace")).hexdigest()[:16]

    @property
    def has_seeders(self) -> bool:
        return self.seeders > 0

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> SearchHit | None:
        file_name = str(raw.get("fileName") or "").strip()
        file_url = str(raw.get("fileUrl") or "").strip()
        download_url = str(raw.get("downloadUrl") or "").strip()
        if not file_name or not (file_url or download_url):
            return None
        return cls(
            file_name=file_name,
            file_url=file_url,
            download_url=download_url or file_url,
            details_url=str(raw.get("detailsUrl") or raw.get("descrLink") or "").strip(),
            site_url=str(raw.get("siteUrl") or "").strip(),
            file_size=_to_int(raw.get("fileSize")),
            seeders=_to_int(raw.get("nbSeeders")),
            leechers=_to_int(raw.get("nbLeechers")),
            category=str(raw.get("category") or "").strip(),
            channel=str(raw.get("channel") or "").strip(),
            subcategory=str(raw.get("subcategory") or "").strip(),
        )


@dataclass(frozen=True)
class Torrent:
    """The state of one torrent inside qBittorrent."""

    hash: str
    name: str
    state: str
    progress: float
    size: int = 0
    downloaded: int = 0
    uploaded: int = 0
    amount_left: int = 0
    dlspeed: int = 0
    upspeed: int = 0
    eta: int = 0
    ratio: float = 0.0
    num_seeders: int = 0
    num_leechers: int = 0
    save_path: str = ""
    category: str = ""
    tags: str = ""
    added_on: int = 0
    completed_on: int = 0
    seeding_time: int = 0
    seeding_time_limit: int = 0
    ratio_limit: float = 0.0
    content_path: str = ""

    @property
    def is_complete(self) -> bool:
        return self.progress >= 0.999999 or self.state in _SEEDING_STATES

    @property
    def is_downloading(self) -> bool:
        return not self.is_complete and self.state not in _STOPPED_STATES

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Torrent:
        return cls(
            hash=str(raw.get("hash") or ""),
            name=str(raw.get("name") or ""),
            state=str(raw.get("state") or "unknown"),
            progress=_to_float(raw.get("progress")),
            size=_to_int(raw.get("size")),
            downloaded=_to_int(raw.get("downloaded")),
            uploaded=_to_int(raw.get("uploaded")),
            amount_left=_to_int(raw.get("amount_left")),
            dlspeed=_to_int(raw.get("dlspeed")),
            upspeed=_to_int(raw.get("upspeed")),
            eta=_to_int(raw.get("eta")),
            ratio=_to_float(raw.get("ratio")),
            num_seeders=_to_int(raw.get("num_seeders")),
            num_leechers=_to_int(raw.get("num_leechers")),
            save_path=str(raw.get("save_path") or ""),
            category=str(raw.get("category") or ""),
            tags=str(raw.get("tags") or ""),
            added_on=_to_int(raw.get("added_on")),
            completed_on=_to_int(raw.get("completed_on")),
            seeding_time=_to_int(raw.get("seeding_time")),
            seeding_time_limit=_to_int(raw.get("seeding_time_limit")),
            ratio_limit=_to_float(raw.get("ratio_limit")),
            content_path=str(raw.get("content_path") or ""),
        )


#: Torrent states that mean "the download finished and we are seeding".
_SEEDING_STATES = {
    "uploading",
    "forcedup",
    "stalledup",
    "pausedup",
    "queuedup",
    "checkingup",
}

#: Torrent states that mean "nothing is happening right now".
_STOPPED_STATES = {"pausedup", "pauseddl", "paused", "stoppedup", "stoppeddl", "error"}


def _version_tuple(raw: str) -> tuple[int, ...]:
    cleaned = raw.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


class QBittorrentClient:
    """Thin wrapper around the qBittorrent WebUI endpoints we need."""

    def __init__(
        self,
        base_url: str,
        username: str = "admin",
        password: str = "",
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session = requests.Session()
        self._session.verify = verify_ssl
        # qBittorrent rejects requests without a matching Referer/Origin header.
        self._session.headers.update({"Referer": self.base_url, "Origin": self.base_url})
        self._auth_lock = threading.Lock()
        self._logged_in = False
        self._version: tuple[int, ...] | None = None
        self._info_cache: tuple[float, list[Torrent]] | None = None
        self._info_cache_ttl = 1.0

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _api_url(self, path: str) -> str:
        return f"{self.base_url}/api/v2/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> requests.Response:
        try:
            response = self._session.request(
                method,
                self._api_url(path),
                params=params,
                data=data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise QBittorrentUnavailable(
                f"Could not reach qBittorrent at {self.base_url}: {exc}"
            ) from exc

        if response.status_code == 403 and retry_auth:
            # The session expired (or the WebUI restarted) - log in again.
            self.login(force=True)
            return self._request(method, path, params=params, data=data, retry_auth=False)

        if response.status_code >= 400:
            raise QBittorrentError(
                f"{method} {path} failed with HTTP {response.status_code}: {response.text[:200]}"
            )
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # authentication
    # ------------------------------------------------------------------
    def login(self, *, force: bool = False) -> None:
        """Authenticate against the WebUI and keep the session cookie."""
        with self._auth_lock:
            if self._logged_in and not force:
                return
            try:
                response = self._session.post(
                    self._api_url("auth/login"),
                    data={"username": self.username, "password": self.password},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise QBittorrentUnavailable(
                    f"Could not reach qBittorrent at {self.base_url}: {exc}"
                ) from exc

            if response.status_code == 403:
                raise QBittorrentAuthError(
                    "qBittorrent refused the login - the client IP may be banned."
                )
            if response.status_code >= 400:
                raise QBittorrentError(
                    f"Login failed with HTTP {response.status_code}: {response.text[:200]}"
                )
            body = response.text.strip().lower()
            if body.startswith("fails"):
                raise QBittorrentAuthError(
                    "Invalid qBittorrent username or password."
                )
            self._logged_in = True

    def logout(self) -> None:
        try:
            self._request("POST", "auth/logout", retry_auth=False)
        except QBittorrentError:  # pragma: no cover - best effort
            log.debug("Logout failed, ignoring", exc_info=True)
        self._logged_in = False

    def _ensure_authenticated(self) -> None:
        if not self._logged_in:
            self.login()

    # ------------------------------------------------------------------
    # application information
    # ------------------------------------------------------------------
    def version(self) -> tuple[int, ...]:
        """The qBittorrent version as a comparable tuple, e.g. ``(5, 0, 4)``."""
        if self._version is None:
            self._ensure_authenticated()
            raw = self._request("GET", "app/version").text.strip()
            self._version = _version_tuple(raw)
        return self._version

    def supports_share_limits(self) -> bool:
        """qBittorrent 5.0 replaced ``setShareRatio`` with ``setShareLimits``."""
        return self.version() >= (5, 0)

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def search(
        self,
        pattern: str,
        *,
        category: str | None = None,
        plugins: str | None = None,
        timeout: float = 45.0,
        limit: int = 0,
    ) -> list[SearchHit]:
        """Run a search across the enabled qBittorrent search plugins."""
        self._ensure_authenticated()
        if self.version() >= (5, 0):
            raw = self._search_v5(pattern, category, plugins, timeout, limit)
        else:
            raw = self._search_legacy(pattern, category, plugins)

        hits: list[SearchHit] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            hit = SearchHit.from_api(item)
            if hit is not None:
                hits.append(hit)
        return hits

    def _search_v5(
        self,
        pattern: str,
        category: str | None,
        plugins: str | None,
        timeout: float,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "pattern": pattern,
            "plugins": plugins or "enabled",
            "category": category or "all",
        }
        started = self._json("POST", "search/start", data=payload)
        job_id = started.get("id") if isinstance(started, dict) else None
        if job_id is None:
            return []

        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                status = self._json("GET", "search/status", params={"id": job_id})
                entries = status if isinstance(status, list) else [status]
                entry = next((e for e in entries if isinstance(e, dict)), None)
                if entry is None:
                    break
                if str(entry.get("status", "")).lower() == "stopped":
                    break
                time.sleep(0.5)
        finally:
            try:
                self._request("POST", "search/stop", data={"id": job_id})
            except QBittorrentError:  # pragma: no cover - best effort
                log.debug("Stopping the search job failed", exc_info=True)

        results = self._json(
            "GET", "search/results", params={"id": job_id, "limit": limit or 0}
        )
        if isinstance(results, dict):
            return [r for r in results.get("results", []) if isinstance(r, dict)]
        return []

    def _search_legacy(
        self,
        pattern: str,
        category: str | None,
        plugins: str | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"pattern": pattern}
        if category:
            params["category"] = category
        if plugins:
            params["plugins"] = plugins
        raw = self._json("GET", "search/index", params=params)
        if isinstance(raw, list):
            return [r for r in raw if isinstance(r, dict)]
        if isinstance(raw, dict):
            return [r for r in raw.get("results", []) if isinstance(r, dict)]
        return []

    # ------------------------------------------------------------------
    # torrent management
    # ------------------------------------------------------------------
    def torrents_info(self, *, use_cache: bool = True) -> list[Torrent]:
        """Every torrent known to qBittorrent."""
        now = time.monotonic()
        if use_cache and self._info_cache is not None:
            stamp, cached = self._info_cache
            if now - stamp < self._info_cache_ttl:
                return cached

        self._ensure_authenticated()
        raw = self._json("GET", "torrents/info")
        torrents = [
            Torrent.from_api(item) for item in raw if isinstance(item, dict)
        ] if isinstance(raw, list) else []
        self._info_cache = (now, torrents)
        return torrents

    def add_torrent(
        self,
        url: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        tags: Sequence[str] | None = None,
        seeding_time_limit_minutes: int | None = None,
        ratio_limit: float | None = None,
        paused: bool = False,
    ) -> bool:
        """Add a torrent from a magnet / .torrent URL.

        Returns ``True`` when qBittorrent accepted the torrent. qBittorrent
        answers with ``Fails.`` when the torrent is already known.
        """
        self._ensure_authenticated()
        data: dict[str, Any] = {"urls": url}
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        if tags:
            data["tags"] = ",".join(tags)
        if seeding_time_limit_minutes is not None:
            data["seedingTimeLimit"] = str(int(seeding_time_limit_minutes))
        if ratio_limit is not None:
            data["ratioLimit"] = str(ratio_limit)
        if paused:
            data["paused"] = "true"

        response = self._request("POST", "torrents/add", data=data)
        return not response.text.strip().lower().startswith("fails")

    def set_share_limits(
        self,
        hashes: str | Iterable[str],
        *,
        ratio_limit: float | None = None,
        seeding_time_limit_minutes: int | None = None,
        inactive_seeding_time_limit_minutes: int | None = None,
    ) -> bool:
        """Apply share limits to one or more torrents.

        Returns ``False`` when the running qBittorrent does not support the
        endpoint (qBittorrent < 5.0 only knows about ratio limits).
        """
        hash_list = [hashes] if isinstance(hashes, str) else list(hashes)
        if not hash_list:
            return False

        data: dict[str, Any] = {"hashes": "|".join(hash_list)}
        if ratio_limit is not None:
            data["ratioLimit"] = str(ratio_limit)
        if seeding_time_limit_minutes is not None:
            data["seedingTimeLimit"] = str(int(seeding_time_limit_minutes))
        if inactive_seeding_time_limit_minutes is not None:
            data["inactiveSeedingTimeLimit"] = str(int(inactive_seeding_time_limit_minutes))

        try:
            self._request("POST", "torrents/setShareLimits", data=data)
        except QBittorrentError as exc:
            if "404" in str(exc):
                return False
            raise
        return True

    def set_share_ratio(self, hashes: str | Iterable[str], ratio_limit: float) -> bool:
        """Legacy (qBittorrent 4.x) share limit endpoint."""
        hash_list = [hashes] if isinstance(hashes, str) else list(hashes)
        if not hash_list:
            return False
        try:
            self._request(
                "POST",
                "torrents/setShareRatio",
                data={"hashes": "|".join(hash_list), "ratioLimit": str(ratio_limit)},
            )
        except QBittorrentError as exc:
            if "404" in str(exc):
                return False
            raise
        return True

    def torrent_properties(self, torrent_hash: str) -> dict[str, Any]:
        properties = self._json(
            "GET", "torrents/properties", params={"hash": torrent_hash}
        )
        return properties if isinstance(properties, dict) else {}

    def pause(self, hashes: str | Iterable[str]) -> None:
        self._request("POST", "torrents/pause", data={"hashes": self._join(hashes)})

    def resume(self, hashes: str | Iterable[str]) -> None:
        self._request("POST", "torrents/resume", data={"hashes": self._join(hashes)})

    def delete(self, hashes: str | Iterable[str], *, delete_files: bool = False) -> None:
        self._request(
            "POST",
            "torrents/delete",
            data={
                "hashes": self._join(hashes),
                "deleteFiles": "true" if delete_files else "false",
            },
        )

    def categories(self) -> dict[str, dict[str, Any]]:
        raw = self._json("GET", "torrents/categories")
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _join(hashes: str | Iterable[str]) -> str:
        if isinstance(hashes, str):
            return hashes
        return "|".join(hashes)
