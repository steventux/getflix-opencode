"""``python -m getflix`` / ``getflix`` entry point."""

from __future__ import annotations

from .app import create_app


def main() -> None:
    app = create_app()
    cfg = app.config["GETFLIX"]
    debug = bool(cfg.debug)

    print(f"Getflix is starting on http://{cfg.host}:{cfg.port}")
    print(f"Talking to qBittorrent at {cfg.qbittorrent_url}")

    # threaded=True lets a long running search coexist with the polling
    # requests that keep the download progress bars up to date.
    app.run(host=cfg.host, port=cfg.port, debug=debug, threaded=True, use_reloader=debug)


if __name__ == "__main__":  # pragma: no cover
    main()
