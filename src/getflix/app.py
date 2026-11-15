"""Application factory for Getflix."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask

from . import util
from .config import Config, load_dotenv_if_present
from .qbittorrent import QBittorrentClient
from .registry import TorrentRegistry

__all__ = ["create_app"]


def create_app(config: Config | None = None) -> Flask:
    """Build the Flask application."""
    load_dotenv_if_present()
    cfg = config or Config.from_env()

    app = Flask(__name__, instance_relative_config=False)
    app.config["GETFLIX"] = cfg
    app.secret_key = os.getenv("GETFLIX_SECRET_KEY") or os.urandom(32)

    client = QBittorrentClient(
        cfg.qbittorrent_url,
        cfg.qbittorrent_username,
        cfg.qbittorrent_password,
        verify_ssl=cfg.qbittorrent_verify_ssl,
        timeout=cfg.qbittorrent_timeout,
    )
    app.extensions["qbit"] = client
    app.extensions["registry"] = TorrentRegistry()

    app.jinja_env.filters["human_size"] = util.human_size
    app.jinja_env.filters["human_speed"] = util.human_speed
    app.jinja_env.filters["human_eta"] = util.human_eta
    app.jinja_env.filters["human_duration"] = util.human_duration
    app.jinja_env.filters["host_from_url"] = util.host_from_url
    app.jinja_env.filters["state_label"] = util.state_label

    from . import views

    app.register_blueprint(views.bp)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {"config": app.config["GETFLIX"]}

    return app
