"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from getflix.app import create_app
from getflix.config import Config
from getflix.qbittorrent import SearchHit, Torrent


@pytest.fixture
def config() -> Config:
    return Config(
        qbittorrent_url="http://qbit.test:8080",
        qbittorrent_username="admin",
        qbittorrent_password="adminadmin",
        max_results=25,
        min_score=45.0,
        seed_hours=24,
    )


@pytest.fixture
def app(config: Config):
    application = create_app(config)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_hit():
    def _make(
        file_name: str = "Dune.Part.Two.2024.1080p.WEB-DL.x264-GROUP",
        seeders: int = 10,
        **kwargs,
    ) -> SearchHit:
        params = {
            "file_name": file_name,
            "file_url": f"https://tracker.test/{file_name}.torrent",
            "download_url": f"magnet:?xt=urn:btih:{abs(hash(file_name)):040x}",
            "site_url": "https://tracker.test",
            "details_url": "https://tracker.test/details/1",
            "file_size": 8 * 1024**3,
            "seeders": seeders,
            "leechers": 2,
        }
        params.update(kwargs)
        return SearchHit(**params)

    return _make


@pytest.fixture
def make_torrent():
    def _make(**kwargs) -> Torrent:
        params = {
            "hash": "a" * 40,
            "name": "Dune.Part.Two.2024.1080p.WEB-DL.x264-GROUP",
            "state": "downloading",
            "progress": 0.42,
            "size": 8 * 1024**3,
            "downloaded": 3 * 1024**3,
            "dlspeed": 1024 * 1024,
            "upspeed": 0,
            "eta": 3600,
            "ratio": 0.1,
            "num_seeders": 5,
            "num_leechers": 1,
            "added_on": 1_700_000_000,
            "seeding_time_limit": 1440,
        }
        params.update(kwargs)
        return Torrent(**params)

    return _make
