"""HTTP routes for Getflix.

Every route except ``/`` returns an HTML fragment that HTMX swaps into the
page - no client side framework required.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from .config import Config
from .fuzzy import media_type, rank_hits
from .qbittorrent import (
    QBittorrentClient,
    QBittorrentError,
    SearchHit,
    Torrent,
)
from .registry import TorrentRegistry, find_torrent
from .util import human_eta, human_speed, state_label

log = logging.getLogger(__name__)

bp = Blueprint("getflix", __name__)

#: URL schemes qBittorrent is able to consume.
_ALLOWED_URL_PREFIXES = ("http://", "https://", "magnet:", "bc://bt/")

def _client() -> QBittorrentClient:
    return current_app.extensions["qbit"]


def _registry() -> TorrentRegistry:
    return current_app.extensions["registry"]


def _config() -> Config:
    return current_app.config["GETFLIX"]


def _allowed_url(url: str) -> bool:
    return url.lower().startswith(_ALLOWED_URL_PREFIXES)


def _progress_view(torrent: Torrent | None) -> dict[str, Any]:
    """Progress bar state for a single torrent."""
    if torrent is None:
        return {"p_pct": 0.0, "p_label": "", "p_polling": False}

    pct = round(torrent.progress * 100, 1)
    if torrent.is_complete:
        label = (
            f"Seeding - ratio {torrent.ratio:.2f} - {human_speed(torrent.upspeed)} up"
        )
        polling = False
    else:
        label = (
            f"{state_label(torrent.state)} - {human_speed(torrent.dlspeed)} down"
            f" - ETA {human_eta(torrent.eta)}"
        )
        polling = True
    return {"p_pct": pct, "p_label": label, "p_polling": polling}


def _row(
    hit: SearchHit,
    score: float,
    torrent: Torrent | None,
    *,
    status: str = "",
    status_kind: str = "",
    added: bool = False,
) -> dict[str, Any]:
    """Build the view model used by the result templates."""
    progress = _progress_view(torrent)
    if torrent is not None:
        button_label = state_label(torrent.state)
    elif added:
        button_label = "Added"
    elif status_kind == "error":
        button_label = "Failed"
    else:
        button_label = "Download"

    return {
        "id": hit.id,
        "hit": hit,
        "score": score,
        "media_type": media_type(hit.file_name),
        "torrent": torrent,
        "status": status,
        "status_kind": status_kind,
        "added": added,
        "state_label": state_label(torrent.state) if torrent else "",
        "p_pct": progress["p_pct"],
        "p_label": progress["p_label"],
        "p_polling": progress["p_polling"],
        "button_label": button_label,
        "button_disabled": bool(torrent) or added or status_kind == "error",
    }


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
@bp.get("/")
def index() -> str:
    client = _client()
    version = ""
    error = ""
    try:
        version = ".".join(str(part) for part in client.version())
    except QBittorrentError as exc:
        error = str(exc)

    # A shared link such as /?q=dune shows the results right away.
    query = (request.args.get("q") or "").strip()
    results: list[dict[str, Any]] = []
    search_error = ""
    if query:
        results, search_error = _run_search(query)

    return render_template(
        "index.html",
        qbittorrent_url=client.base_url,
        qbittorrent_version=version,
        connection_error=error,
        seed_hours=_config().seed_hours,
        query=query,
        results=results,
        error=search_error,
        kind="any",
        relax=False,
        min_score=_config().min_score,
        total=len(results),
    )


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def _run_search(
    query: str,
    *,
    kind: str = "any",
    relax: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Search qBittorrent and return the ranked rows plus an error message."""
    cfg = _config()
    client = _client()
    registry = _registry()

    try:
        hits = client.search(
            query,
            timeout=cfg.search_timeout,
            limit=max(cfg.max_results * 4, 200),
        )
    except QBittorrentError as exc:
        log.warning("Search for %r failed: %s", query, exc)
        return [], str(exc)

    min_score = 0.0 if relax else cfg.min_score
    ranked = rank_hits(
        query,
        hits,
        min_score=min_score,
        limit=cfg.max_results,
        categories=cfg.categories,
    )
    if kind != "any":
        ranked = [(score, hit) for score, hit in ranked if media_type(hit.file_name) == kind]

    try:
        torrents = client.torrents_info()
    except QBittorrentError:
        torrents = []

    results: list[dict[str, Any]] = []
    for score, hit in ranked:
        registry.remember(hit)
        results.append(_row(hit, score, find_torrent(hit.file_name, torrents)))
    return results, ""


@bp.get("/search")
def search() -> str:
    cfg = _config()
    query = (request.args.get("q") or "").strip()
    relax = request.args.get("relax") == "1"
    kind = (request.args.get("kind") or "any").lower()
    if kind not in {"any", "movie", "tv"}:
        kind = "any"

    results: list[dict[str, Any]] = []
    error = ""
    if query:
        results, error = _run_search(query, kind=kind, relax=relax)

    return render_template(
        "partials/search_results.html",
        query=query,
        results=results,
        kind=kind,
        relax=relax,
        error=error,
        min_score=cfg.min_score,
        total=len(results),
    )


# ---------------------------------------------------------------------------
# adding torrents
# ---------------------------------------------------------------------------
@bp.post("/torrents")
def add_torrent() -> str:
    cfg = _config()
    client = _client()
    registry = _registry()

    result_id = (request.form.get("result_id") or "").strip()
    url = (request.form.get("url") or "").strip()
    name = (request.form.get("name") or "").strip()

    entry = registry.get(result_id)
    hit = entry.hit if entry else None
    if hit is not None:
        url = hit.download_url or hit.file_url
        name = name or hit.file_name
    if not name:
        name = result_id

    if not _allowed_url(url):
        message = "That download link is not something qBittorrent can fetch."
        registry.mark_error(result_id, message)
        return _render_card(result_id, hit, name, message, "error", added=False)

    try:
        added = client.add_torrent(
            url,
            save_path=cfg.save_path,
            category=cfg.category,
            tags=cfg.tags,
            seeding_time_limit_minutes=cfg.seeding_time_limit_minutes,
        )
    except QBittorrentError as exc:
        log.warning("Adding %r failed: %s", name, exc)
        registry.mark_error(result_id, str(exc))
        return _render_card(result_id, hit, name, str(exc), "error", added=False)

    registry.mark_added(result_id, name, url)

    if not added:
        message = "qBittorrent already knows this torrent."
        registry.mark_error(result_id, message)
        return _render_card(result_id, hit, name, message, "warning", added=True)

    message = f"Added to qBittorrent - it will seed for {cfg.seed_hours} hours."

    # The torrent may need a moment to show up in qBittorrent.
    torrent = None
    try:
        torrents = client.torrents_info(use_cache=False)
        torrent = find_torrent(name, torrents)
        if torrent is not None:
            registry.bind_hash(result_id, torrent.hash)
    except QBittorrentError:
        torrent = None

    return _render_card(result_id, hit, name, message, "", added=True, torrent=torrent)


def _render_card(
    result_id: str,
    hit: SearchHit | None,
    name: str,
    message: str,
    kind: str,
    *,
    added: bool = False,
    torrent: Torrent | None = None,
) -> str:
    """Render a single result row (used as the response of the add action)."""
    registry = _registry()
    if hit is None:
        entry = registry.get(result_id)
        if entry is not None:
            hit = entry.hit
    if hit is None:
        hit = SearchHit(file_name=name, file_url="", download_url="")

    row = _row(hit, 0.0, torrent, status=message, status_kind=kind, added=added)
    return render_template("partials/result_card.html", r=row)


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------
@bp.get("/progress/<result_id>")
def progress(result_id: str) -> str:
    client = _client()
    registry = _registry()

    entry = registry.get(result_id)
    if entry is None:
        return render_template(
            "partials/progress.html",
            p_id=result_id,
            p_pct=0.0,
            p_label="Unknown result",
            p_polling=False,
            p_button=None,
        )

    try:
        torrents = client.torrents_info()
        _, torrent = registry.resolve(result_id, torrents)
    except QBittorrentError as exc:
        return render_template(
            "partials/progress.html",
            p_id=result_id,
            p_pct=0.0,
            p_label=f"qBittorrent unavailable: {exc}",
            p_polling=True,
            p_button=None,
        )

    if torrent is None:
        return render_template(
            "partials/progress.html",
            p_id=result_id,
            p_pct=0.0,
            p_label="Waiting for qBittorrent...",
            p_polling=True,
            p_button={"id": result_id, "label": "Starting..."},
        )

    view = _progress_view(torrent)
    button = {
        "id": result_id,
        "label": state_label(torrent.state) if not torrent.is_complete else "Downloaded",
    }
    return render_template(
        "partials/progress.html",
        p_id=result_id,
        p_pct=view["p_pct"],
        p_label=view["p_label"],
        p_polling=view["p_polling"],
        p_button=button,
    )


# ---------------------------------------------------------------------------
# download list
# ---------------------------------------------------------------------------
@bp.get("/downloads")
def downloads() -> str:
    client = _client()
    try:
        torrents = client.torrents_info()
    except QBittorrentError as exc:
        return render_template(
            "partials/downloads.html",
            torrents=[],
            active=[],
            completed=[],
            error=str(exc),
            seed_hours=_config().seed_hours,
        )

    active = sorted(
        (t for t in torrents if not t.is_complete),
        key=lambda t: (-t.added_on, t.name.lower()),
    )
    completed = sorted(
        (t for t in torrents if t.is_complete),
        key=lambda t: (-t.completed_on, -t.added_on, t.name.lower()),
    )
    return render_template(
        "partials/downloads.html",
        torrents=torrents,
        active=active,
        completed=completed,
        error="",
        seed_hours=_config().seed_hours,
    )


@bp.post("/torrents/<torrent_hash>/pause")
def pause_torrent(torrent_hash: str) -> str:
    client = _client()
    try:
        client.pause(torrent_hash)
    except QBittorrentError as exc:
        log.warning("Pausing %s failed: %s", torrent_hash, exc)
    return downloads()


@bp.post("/torrents/<torrent_hash>/resume")
def resume_torrent(torrent_hash: str) -> str:
    client = _client()
    try:
        client.resume(torrent_hash)
    except QBittorrentError as exc:
        log.warning("Resuming %s failed: %s", torrent_hash, exc)
    return downloads()


@bp.delete("/torrents/<torrent_hash>")
def delete_torrent(torrent_hash: str) -> str:
    client = _client()
    try:
        client.delete(torrent_hash, delete_files=False)
    except QBittorrentError as exc:
        log.warning("Deleting %s failed: %s", torrent_hash, exc)
    return downloads()


@bp.post("/torrents/<torrent_hash>/seed")
def seed_torrent(torrent_hash: str) -> str:
    """Mark a torrent to seed for the configured number of days."""
    client = _client()
    cfg = _config()
    minutes = cfg.seeding_time_limit_minutes
    try:
        if client.supports_share_limits():
            client.set_share_limits(torrent_hash, seeding_time_limit_minutes=minutes)
        else:
            # qBittorrent 4.x only knows ratio based share limits.
            client.set_share_ratio(torrent_hash, -1)
    except QBittorrentError as exc:
        log.warning("Setting the share limit for %s failed: %s", torrent_hash, exc)
    return downloads()


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------
@bp.get("/api/status")
def status() -> Any:
    """A tiny JSON status endpoint, handy for debugging."""
    client = _client()
    payload: dict[str, Any] = {"url": client.base_url}
    try:
        payload["version"] = ".".join(str(part) for part in client.version())
        payload["torrents"] = len(client.torrents_info())
        payload["ok"] = True
    except QBittorrentError as exc:
        payload["ok"] = False
        payload["error"] = str(exc)
    return jsonify(payload)


@bp.get("/healthz")
def healthz() -> Any:
    """Liveness probe, used by the container health check.

    It only reports that the web application itself is alive, it does not
    depend on qBittorrent being reachable.
    """
    return jsonify({"ok": True})
