"""Tests for playing completed downloads."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from getflix.library import (
    PlayableFileNotFound,
    content_type_for,
    resolve_playable_file,
)
from getflix.qbittorrent import QBittorrentError


class FakeLibrary:
    """Only the part of the qBittorrent client the playback routes use."""

    def __init__(self) -> None:
        self.torrents: list = []
        self.error: Exception | None = None

    def torrents_info(self, use_cache: bool = True):
        if self.error is not None:
            raise self.error
        return list(self.torrents)


@pytest.fixture
def player(app, tmp_path, monkeypatch):
    """Point the app at a temporary download directory."""
    app.config["GETFLIX"] = replace(
        app.config["GETFLIX"],
        download_root=str(tmp_path),
        qbittorrent_download_root=str(tmp_path),
    )
    fake = FakeLibrary()
    monkeypatch.setitem(app.extensions, "qbit", fake)
    return fake, tmp_path


def _finished(make_torrent, path: Path, **kwargs):
    return make_torrent(state="uploading", progress=1.0, content_path=str(path), **kwargs)


def test_playlist_points_the_player_at_the_stream(client, player, make_torrent):
    fake, root = player
    movie = root / "Dune.Part.Two.2024.1080p.WEB-DL.x264-GROUP.mkv"
    movie.write_bytes(b"0123456789")
    fake.torrents.append(_finished(make_torrent, movie, hash="a" * 40))

    response = client.get(f"/play/{'a' * 40}/playlist.m3u")

    assert response.status_code == 200
    assert response.mimetype == "audio/x-mpegurl"
    assert response.get_data(as_text=True).splitlines() == [
        "#EXTM3U",
        "#EXTINF:-1,Dune.Part.Two.2024.1080p.WEB-DL.x264-GROUP",
        f"http://localhost/play/{'a' * 40}",
    ]
    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert disposition.endswith('.m3u"')


def test_stream_serves_the_file(client, player, make_torrent):
    fake, root = player
    movie = root / "Dune.mkv"
    movie.write_bytes(b"0123456789")
    fake.torrents.append(_finished(make_torrent, movie, hash="b" * 40))

    response = client.get(f"/play/{'b' * 40}")

    assert response.status_code == 200
    assert response.data == b"0123456789"
    assert response.mimetype == "video/x-matroska"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["Content-Disposition"].startswith("inline")


def test_stream_supports_range_requests(client, player, make_torrent):
    """VLC seeks with range requests, without them it re-downloads."""
    fake, root = player
    movie = root / "Dune.mp4"
    movie.write_bytes(b"0123456789")
    fake.torrents.append(_finished(make_torrent, movie, hash="c" * 40))

    response = client.get(f"/play/{'c' * 40}", headers={"Range": "bytes=2-5"})

    assert response.status_code == 206
    assert response.data == b"2345"
    assert response.headers["Content-Range"] == "bytes 2-5/10"


def test_multi_file_torrent_plays_the_largest_video(client, player, make_torrent):
    fake, root = player
    folder = root / "Some.Show.S01"
    (folder / "sample").mkdir(parents=True)
    (folder / "sample" / "sample.mkv").write_bytes(b"x" * 10)
    (folder / "readme.txt").write_text("not a video")
    episode = folder / "Episode.01.mkv"
    episode.write_bytes(b"y" * 100)
    fake.torrents.append(_finished(make_torrent, folder, hash="d" * 40))

    response = client.get(f"/play/{'d' * 40}")

    assert response.status_code == 200
    assert response.data == b"y" * 100


def test_a_download_that_is_not_finished_cannot_be_played(client, player, make_torrent):
    fake, root = player
    movie = root / "Dune.mkv"
    movie.write_bytes(b"partial")
    fake.torrents.append(
        make_torrent(
            hash="e" * 40, state="downloading", progress=0.5, content_path=str(movie)
        )
    )

    assert client.get(f"/play/{'e' * 40}").status_code == 409
    assert client.get(f"/play/{'e' * 40}/playlist.m3u").status_code == 409


def test_an_unknown_torrent_returns_404(client, player):
    assert client.get(f"/play/{'f' * 40}").status_code == 404
    assert client.get(f"/play/{'f' * 40}/playlist.m3u").status_code == 404


def test_a_file_outside_the_download_root_is_refused(client, player, make_torrent):
    fake, root = player
    outside = root.parent / f"{root.name}-secret.mkv"
    outside.write_bytes(b"secret")
    fake.torrents.append(_finished(make_torrent, outside, hash="1" * 40))

    assert client.get(f"/play/{'1' * 40}").status_code == 404


def test_a_missing_file_returns_404(client, player, make_torrent):
    fake, root = player
    fake.torrents.append(_finished(make_torrent, root / "gone.mkv", hash="2" * 40))

    assert client.get(f"/play/{'2' * 40}").status_code == 404


def test_a_folder_without_a_video_returns_404(client, player, make_torrent):
    fake, root = player
    folder = root / "no-video"
    folder.mkdir()
    (folder / "notes.txt").write_text("nothing to play")
    fake.torrents.append(_finished(make_torrent, folder, hash="3" * 40))

    assert client.get(f"/play/{'3' * 40}").status_code == 404


def test_the_playlist_filename_drops_the_video_extension(client, player, make_torrent):
    fake, root = player
    movie = root / "Dune Part Two.mkv"
    movie.write_bytes(b"x")
    fake.torrents.append(_finished(make_torrent, movie, hash="8" * 40, name=movie.name))

    response = client.get(f"/play/{'8' * 40}/playlist.m3u")

    disposition = response.headers["Content-Disposition"]
    assert disposition == 'attachment; filename="Dune_Part_Two.m3u"'


def test_a_single_file_that_is_not_a_video_returns_404(client, player, make_torrent):
    fake, root = player
    archive = root / "Dune.2024.rar"
    archive.write_bytes(b"not a video")
    fake.torrents.append(_finished(make_torrent, archive, hash="7" * 40))

    assert client.get(f"/play/{'7' * 40}").status_code == 404


def test_qbittorrent_being_down_returns_503(client, player):
    fake, _ = player
    fake.error = QBittorrentError("connection refused")

    assert client.get(f"/play/{'4' * 40}").status_code == 503


def test_completed_names_are_vlc_links(client, player, make_torrent):
    fake, root = player
    movie = root / "Dune.mkv"
    movie.write_bytes(b"x")
    torrent_hash = "5" * 40
    fake.torrents.append(
        _finished(make_torrent, movie, hash=torrent_hash, name="Dune (2021)")
    )

    body = client.get("/downloads").get_data(as_text=True)
    name_link = re.search(
        r'<a\s+class="download-name download-name-link"\s+href="([^"]+)"', body
    )

    assert name_link is not None
    assert name_link.group(1) == f"vlc:http://localhost/play/{torrent_hash}"
    assert "Play Dune (2021) in VLC" in body
    # The .m3u playlist stays available for people without the handler.
    assert f'href="/play/{torrent_hash}/playlist.m3u"' in body
    assert ">Playlist</a>" in body


def test_the_playlist_can_be_the_primary_link(client, player, make_torrent, app):
    fake, root = player
    movie = root / "Dune.mkv"
    movie.write_bytes(b"x")
    torrent_hash = "9" * 40
    fake.torrents.append(_finished(make_torrent, movie, hash=torrent_hash))
    app.config["GETFLIX"] = replace(
        app.config["GETFLIX"], vlc_url_scheme=False
    )

    body = client.get("/downloads").get_data(as_text=True)
    name_link = re.search(
        r'<a\s+class="download-name download-name-link"\s+href="([^"]+)"', body
    )

    assert name_link is not None
    assert name_link.group(1) == f"/play/{torrent_hash}/playlist.m3u"
    assert "vlc:http" not in body


def test_names_of_running_downloads_are_not_links(client, player, make_torrent):
    fake, _ = player
    fake.torrents.append(make_torrent(hash="6" * 40, state="downloading", progress=0.2))

    body = client.get("/downloads").get_data(as_text=True)

    assert "/play/" not in body


# ---------------------------------------------------------------------------
# the path resolution itself
# ---------------------------------------------------------------------------
def test_a_path_from_the_qbittorrent_container_is_remapped(tmp_path):
    local = tmp_path / "host"
    movie = local / "Movies" / "Dune.mkv"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"x")

    resolved = resolve_playable_file(
        "/data/Movies/Dune.mkv", local, qbittorrent_root="/data"
    )

    assert resolved == movie


def test_a_relative_path_is_resolved_against_the_root(tmp_path):
    movie = tmp_path / "Dune.mkv"
    movie.write_bytes(b"x")

    assert resolve_playable_file("Dune.mkv", tmp_path) == movie


def test_a_symlink_that_escapes_the_root_is_refused(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.mkv"
    outside.write_bytes(b"x")
    link = tmp_path / "link.mkv"
    link.symlink_to(outside)

    with pytest.raises(PlayableFileNotFound):
        resolve_playable_file(str(link), tmp_path)


def test_an_empty_content_path_is_refused(tmp_path):
    with pytest.raises(PlayableFileNotFound):
        resolve_playable_file("", tmp_path)


def test_content_types_for_containers_python_gets_wrong():
    assert content_type_for(Path("movie.mkv")) == "video/x-matroska"
    assert content_type_for(Path("stream.ts")) == "video/mp2t"
    assert content_type_for(Path("clip.mp4")) == "video/mp4"
