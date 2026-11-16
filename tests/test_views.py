"""Tests for the HTTP routes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from getflix.qbittorrent import QBittorrentError


class FakeQBit:
    """A stand in for :class:`QBittorrentClient`."""

    def __init__(self, hits=None, torrents=None, version=(5, 0, 4)):
        self.hits = hits or []
        self.torrents = torrents or []
        self.version_tuple = version
        self.base_url = "http://qbit.test:8080"
        self.added: list[dict] = []
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.deleted: list[str] = []
        self.share_limits: list[dict] = []
        self.search_error: Exception | None = None
        self.add_error: Exception | None = None
        self.add_result = True

    # -- api -----------------------------------------------------------
    def version(self):
        return self.version_tuple

    def supports_share_limits(self):
        return self.version_tuple >= (5, 0)

    def search(self, pattern, **kwargs):
        if self.search_error:
            raise self.search_error
        return list(self.hits)

    def torrents_info(self, use_cache=True):
        return list(self.torrents)

    def add_torrent(self, url, **kwargs):
        if self.add_error:
            raise self.add_error
        self.added.append({"url": url, **kwargs})
        return self.add_result

    def set_share_limits(self, hashes, **kwargs):
        self.share_limits.append({"hashes": hashes, **kwargs})
        return True

    def set_share_ratio(self, hashes, ratio_limit):
        self.share_limits.append({"hashes": hashes, "ratioLimit": ratio_limit})
        return True

    def pause(self, hashes):
        self.paused.append(hashes)

    def resume(self, hashes):
        self.resumed.append(hashes)

    def delete(self, hashes, delete_files=False):
        self.deleted.append(hashes)


@pytest.fixture
def fake(app, monkeypatch):
    fake = FakeQBit()
    monkeypatch.setitem(app.extensions, "qbit", fake)
    return fake


def test_index_renders(client, fake):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Getflix" in body
    assert "qBittorrent 5.0.4" in body


def test_the_version_links_to_the_qbittorrent_webui(client, fake):
    body = client.get("/").get_data(as_text=True)

    assert 'href="http://qbit.test:8080"' in body
    assert 'title="Open the qBittorrent web UI"' in body
    assert "qBittorrent 5.0.4" in body


def test_the_webui_link_can_be_configured_separately(app, client, fake):
    app.config["GETFLIX"] = replace(
        app.config["GETFLIX"],
        qbittorrent_webui_url="http://127.0.0.1:8080/",
    )

    body = client.get("/").get_data(as_text=True)

    assert 'href="http://127.0.0.1:8080"' in body


def test_search_returns_matching_results(client, fake, make_hit):
    fake.hits = [
        make_hit("Dune.2021.1080p.BluRay.x264", seeders=25),
        make_hit("Dune.2021.720p.WEBRip.x264", seeders=0),
        make_hit("Interstellar.2014.1080p.BluRay.x264", seeders=90),
    ]
    response = client.get("/search?q=dune")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Dune.2021.1080p.BluRay.x264" in body
    # The result without seeders is filtered out.
    assert "Dune.2021.720p.WEBRip.x264" not in body
    # So is the unrelated movie.
    assert "Interstellar" not in body


def test_search_reports_qbittorrent_errors(client, fake):
    fake.search_error = QBittorrentError("WebUI is down")
    response = client.get("/search?q=dune")
    assert response.status_code == 200
    assert "WebUI is down" in response.get_data(as_text=True)


def test_search_without_a_query_renders_no_results_or_hint(client):
    response = client.get("/search")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Type a movie" not in body
    assert 'id="results"' in body


def test_search_can_be_filtered_by_media_type(client, fake, make_hit):
    fake.hits = [
        make_hit("The.Office.S03E04.HDTV.x264"),
        make_hit("The.Office.2021.1080p.BluRay.x264"),
    ]
    body = client.get("/search?q=the+office&kind=tv").get_data(as_text=True)
    assert "The.Office.S03E04.HDTV.x264" in body
    assert "The.Office.2021.1080p.BluRay.x264" not in body


def test_adding_a_torrent_starts_the_download(client, fake, make_hit):
    hit = make_hit("Dune.2021.1080p.BluRay.x264")
    fake.hits = [hit]
    client.get("/search?q=dune")

    response = client.post(
        "/torrents",
        data={
            "result_id": hit.id,
            "url": hit.download_url,
            "name": hit.file_name,
        },
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Added to qBittorrent" in body

    assert len(fake.added) == 1
    assert fake.added[0]["url"] == hit.download_url
    # The torrent is limited to one day of seeding.
    assert fake.added[0]["seeding_time_limit_minutes"] == 1440


def test_adding_a_torrent_reports_failures(client, fake, make_hit):
    fake.add_error = QBittorrentError("nope")
    hit = make_hit()
    body = client.post(
        "/torrents",
        data={"result_id": hit.id, "url": hit.download_url, "name": hit.file_name},
    ).get_data(as_text=True)
    assert "nope" in body


def test_adding_rejects_unsupported_urls(client, fake):
    body = client.post(
        "/torrents",
        data={"result_id": "abc", "url": "file:///etc/passwd", "name": "x"},
    ).get_data(as_text=True)
    assert "not something qBittorrent can fetch" in body
    assert fake.added == []


def test_progress_endpoint_reports_the_download_state(client, fake, make_hit, make_torrent):
    hit = make_hit()
    fake.hits = [hit]
    client.get("/search?q=dune")

    torrent = make_torrent(hash="b" * 40, progress=0.25)
    fake.torrents = [torrent]

    client.post(
        "/torrents",
        data={"result_id": hit.id, "url": hit.download_url, "name": hit.file_name},
    )

    body = client.get(f"/progress/{hit.id}").get_data(as_text=True)
    assert "25.0%" in body
    assert "down" in body


def test_progress_endpoint_stops_polling_when_complete(client, fake, make_hit, make_torrent):
    hit = make_hit()
    fake.hits = [hit]
    client.get("/search?q=dune")

    fake.torrents = [make_torrent(hash="c" * 40, progress=1.0, state="uploading")]
    client.post(
        "/torrents",
        data={"result_id": hit.id, "url": hit.download_url, "name": hit.file_name},
    )

    body = client.get(f"/progress/{hit.id}").get_data(as_text=True)
    assert "Seeding" in body
    assert "hx-trigger" not in body


def test_downloads_panel_lists_active_and_completed(client, fake, make_torrent):
    fake.torrents = [
        make_torrent(hash="a" * 40, name="Active", state="downloading", progress=0.4),
        make_torrent(hash="b" * 40, name="Done", state="uploading", progress=1.0),
    ]
    body = client.get("/downloads").get_data(as_text=True)
    assert "Active" in body
    assert "Done" in body
    assert "Completed" in body


def test_pause_resume_and_delete(client, fake, make_torrent):
    fake.torrents = [make_torrent(hash="d" * 40)]
    client.post("/torrents/" + "d" * 40 + "/pause")
    client.post("/torrents/" + "d" * 40 + "/resume")
    client.delete("/torrents/" + "d" * 40)

    assert fake.paused == ["d" * 40]
    assert fake.resumed == ["d" * 40]
    assert fake.deleted == ["d" * 40]


def test_seeding_for_one_day_uses_the_share_limit_endpoint(client, fake, make_torrent):
    fake.torrents = [make_torrent(hash="e" * 40)]
    client.post("/torrents/" + "e" * 40 + "/seed")
    assert fake.share_limits[0]["seeding_time_limit_minutes"] == 1440


def test_seeding_falls_back_to_the_ratio_endpoint_on_qbittorrent_4(client, fake, make_torrent):
    fake.version_tuple = (4, 6, 5)
    fake.torrents = [make_torrent(hash="f" * 40)]
    client.post("/torrents/" + "f" * 40 + "/seed")
    assert fake.share_limits[0]["ratioLimit"] == -1


def test_status_endpoint(client, fake):
    payload = client.get("/api/status").get_json()
    assert payload["ok"] is True
    assert payload["version"] == "5.0.4"


def test_healthz_endpoint(client, fake):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
