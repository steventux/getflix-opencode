"""Integration tests for the qBittorrent client.

A small mock of the qBittorrent WebUI is started on a random port so the
real HTTP client (including the cookie handling and the re-authentication)
is exercised.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from getflix.qbittorrent import (
    QBittorrentAuthError,
    QBittorrentClient,
    QBittorrentUnavailable,
)

SEARCH_RESULTS = [
    {
        "fileName": "Dune.2021.1080p.BluRay.x264",
        "fileSize": 8589934592,
        "fileUrl": "https://tracker.test/dune.torrent",
        "downloadUrl": "magnet:?xt=urn:btih:deadbeef",
        "descrLink": "https://tracker.test/details/1",
        "siteUrl": "https://tracker.test",
        "nbLeechers": 3,
        "nbSeeders": 42,
        "category": "Movies",
    },
    {
        "fileName": "Dune.2021.720p.WEBRip.x264",
        "fileSize": 4294967296,
        "fileUrl": "https://tracker.test/dune720.torrent",
        "nbLeechers": 1,
        "nbSeeders": 0,
    },
]


class MockQBittorrent:
    """A tiny in-memory stand in for the qBittorrent WebUI."""

    def __init__(self, version: str = "v5.0.4") -> None:
        self.version = version
        self.torrents: list[dict[str, Any]] = []
        self.added: list[dict[str, Any]] = []
        self.share_limits: list[dict[str, Any]] = []
        self.logins: list[dict[str, str]] = []
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.deleted: list[str] = []
        self.session_id = "mock-sid"
        self.reject_requests = False
        self.app = Flask("mock-qbit")

        self._register_routes()

    def _register_routes(self) -> None:
        app = self.app

        @app.post("/api/v2/auth/login")
        def login():
            self.logins.append(dict(request.form))
            if request.form.get("password") == "wrong":
                return "Fails.", 200
            # A fresh login makes the session valid again.
            self.reject_requests = False
            response = app.make_response("Ok.")
            response.set_cookie("SID", self.session_id)
            return response

        @app.get("/api/v2/app/version")
        def version():
            return self.version

        @app.post("/api/v2/search/start")
        def search_start():
            return jsonify({"id": 1})

        @app.get("/api/v2/search/status")
        def search_status():
            return jsonify([{"id": 1, "status": "Stopped", "total": 2}])

        @app.get("/api/v2/search/results")
        def search_results():
            return jsonify(
                {"results": SEARCH_RESULTS, "status": "Stopped", "total": 2}
            )

        @app.get("/api/v2/search/index")
        def search_index():
            return jsonify(SEARCH_RESULTS)

        @app.get("/api/v2/torrents/info")
        def torrents_info():
            return jsonify(self.torrents)

        @app.post("/api/v2/torrents/add")
        def torrents_add():
            self.added.append(dict(request.form))
            return "Ok."

        @app.post("/api/v2/torrents/setShareLimits")
        def set_share_limits():
            self.share_limits.append(dict(request.form))
            return ""

        @app.post("/api/v2/torrents/setShareRatio")
        def set_share_ratio():
            self.share_limits.append(dict(request.form))
            return ""

        @app.post("/api/v2/torrents/pause")
        def pause():
            self.paused.append(request.form.get("hashes", ""))
            return ""

        @app.post("/api/v2/torrents/resume")
        def resume():
            self.resumed.append(request.form.get("hashes", ""))
            return ""

        @app.post("/api/v2/torrents/delete")
        def delete():
            self.deleted.append(request.form.get("hashes", ""))
            return ""

        @app.get("/api/v2/torrents/protected")
        def protected():
            if self.reject_requests:
                return "Forbidden", 403
            return "Ok."


@pytest.fixture
def mock_qbit():
    server = MockQBittorrent()
    httpd = make_server("127.0.0.1", 0, server.app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    # Give the server a moment to bind.
    for _ in range(50):
        if httpd.server_port:
            break
        time.sleep(0.02)
    server.url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        yield server
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def qbit_client(mock_qbit):
    return QBittorrentClient(mock_qbit.url, "admin", "adminadmin")


def test_login_sets_the_session_cookie(qbit_client, mock_qbit):
    qbit_client.login()
    assert mock_qbit.logins == [{"username": "admin", "password": "adminadmin"}]


def test_login_with_a_wrong_password_raises(mock_qbit):
    client = QBittorrentClient(mock_qbit.url, "admin", "wrong")
    with pytest.raises(QBittorrentAuthError):
        client.login()


def test_unreachable_qbittorrent_raises():
    client = QBittorrentClient("http://127.0.0.1:1", "admin", "admin")
    with pytest.raises(QBittorrentUnavailable):
        client.login()


def test_version_is_parsed(qbit_client):
    assert qbit_client.version() == (5, 0, 4)
    assert qbit_client.supports_share_limits() is True


def test_search_returns_parsed_hits(qbit_client):
    hits = qbit_client.search("dune")
    assert len(hits) == 2
    assert hits[0].file_name == "Dune.2021.1080p.BluRay.x264"
    assert hits[0].seeders == 42
    assert hits[0].leechers == 3
    assert hits[0].download_url == "magnet:?xt=urn:btih:deadbeef"
    assert hits[0].details_url == "https://tracker.test/details/1"
    assert hits[1].seeders == 0


def test_add_torrent_sends_the_seeding_time_limit(qbit_client, mock_qbit):
    assert qbit_client.add_torrent(
        "magnet:?xt=urn:btih:deadbeef",
        category="movies",
        tags=["getflix"],
        seeding_time_limit_minutes=1440,
    )
    assert mock_qbit.added[0]["urls"] == "magnet:?xt=urn:btih:deadbeef"
    assert mock_qbit.added[0]["category"] == "movies"
    assert mock_qbit.added[0]["tags"] == "getflix"
    assert mock_qbit.added[0]["seedingTimeLimit"] == "1440"


def test_set_share_limits(qbit_client, mock_qbit):
    assert qbit_client.set_share_limits("abc", seeding_time_limit_minutes=1440)
    assert mock_qbit.share_limits[0]["hashes"] == "abc"
    assert mock_qbit.share_limits[0]["seedingTimeLimit"] == "1440"


def test_pause_resume_and_delete(qbit_client, mock_qbit):
    qbit_client.pause("abc")
    qbit_client.resume("abc")
    qbit_client.delete("abc")
    assert mock_qbit.paused == ["abc"]
    assert mock_qbit.resumed == ["abc"]
    assert mock_qbit.deleted == ["abc"]


def test_torrents_info_is_parsed(qbit_client, mock_qbit):
    mock_qbit.torrents = [
        {
            "hash": "a" * 40,
            "name": "Dune.2021.1080p.BluRay.x264",
            "state": "downloading",
            "progress": 0.42,
            "size": 8589934592,
            "dlspeed": 1048576,
            "eta": 3600,
            "num_seeders": 5,
            "seeding_time_limit": 1440,
        }
    ]
    torrents = qbit_client.torrents_info()
    assert len(torrents) == 1
    assert torrents[0].progress == 0.42
    assert torrents[0].is_complete is False
    assert torrents[0].seeding_time_limit == 1440


def test_the_client_reauthenticates_when_the_session_expires(qbit_client, mock_qbit):
    qbit_client.login()
    assert len(mock_qbit.logins) == 1

    # Simulate a WebUI restart: the session cookie is no longer valid.
    mock_qbit.reject_requests = True

    response = qbit_client._request("GET", "torrents/protected")
    assert response.status_code == 200
    # The client logged in again before retrying the request.
    assert len(mock_qbit.logins) == 2
