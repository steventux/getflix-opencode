# Getflix

A small Flask application that puts a clean, Google-like search in front of a
local [qBittorrent](https://www.qbittorrent.org/) instance.

Type a movie or TV series, Getflix asks the qBittorrent search plugins for
candidates, fuzzy-matches them against your query, and lets you start the
download with one click. Progress bars update live while you keep searching.

![Getflix](https://github.com/steventux/getflix/assets/93511/53abcfe3-aac2-4b02-8e44-2ceaa399e14f)

## Features

- **Fuzzy search** for movies and TV series. Release names such as
  `Dune.Part.Two.2024.1080p.WEB-DL.x264-GROUP` are parsed into a title, year,
  season/episode and quality, then scored against your query with
  [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz).
- **Only results with seeders** (`seeders > 0`) are ever listed.
- **One click downloads** - the torrent is handed to qBittorrent over its
  WebUI API and a progress bar appears underneath the result.
- **Keep searching while downloading.** The search, the progress bars and the
  download list are independent HTMX requests, so a slow search never blocks
  the progress of an in-flight download (and vice versa).
- **Download and completed downloads** are both visible in the Downloads
  panel, with pause / resume / remove controls.
- **Seed for a day.** Torrents are added with a maximum seeding time of 24
  hours (1440 minutes) so qBittorrent stops them afterwards. Existing
  torrents can be given the same limit with the *Seed 24h* button.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- A running qBittorrent with the **WebUI** enabled
  (*Tools → Preferences → Web UI*) and at least one search plugin enabled

Alternatively, Docker and Docker Compose are enough to run the whole stack,
including qBittorrent behind a VPN - see [Docker](#docker).

## Installation

```bash
uv sync
```

## Configuration

Copy the example file and adjust it:

```bash
cp .env.example .env
```

| Variable | Default | Description |
| --- | --- | --- |
| `QBITTORRENT_URL` | `http://localhost:8080` | Base URL of the qBittorrent WebUI |
| `QBITTORRENT_USERNAME` | `admin` | WebUI user name |
| `QBITTORRENT_PASSWORD` | `adminadmin` | WebUI password |
| `QBITTORRENT_VERIFY_SSL` | `true` | Set to `false` for self signed certificates |
| `QBITTORRENT_TIMEOUT` | `30` | Per request timeout in seconds |
| `GETFLIX_SEARCH_TIMEOUT` | `45` | How long a single search may run |
| `GETFLIX_MAX_RESULTS` | `50` | Maximum number of results shown |
| `GETFLIX_MIN_SCORE` | `45` | Minimum fuzzy score (0 disables the filter) |
| `GETFLIX_SEED_HOURS` | `24` | Maximum seeding time for new torrents |
| `GETFLIX_SAVE_PATH` | – | Optional default save path |
| `GETFLIX_CATEGORY` | – | Optional qBittorrent category |
| `GETFLIX_TAGS` | `getflix` | Comma separated qBittorrent tags |
| `GETFLIX_CATEGORIES` | – | Only list these qBittorrent search categories |
| `GETFLIX_HOST` / `GETFLIX_PORT` | `127.0.0.1` / `5000` | Where Getflix listens |
| `GETFLIX_SECRET_KEY` | random | Fixed Flask session secret (optional) |
| `GETFLIX_DEBUG` | `false` | Enable the Flask debugger and auto reload |

## Running

```bash
uv run getflix
# or
uv run python -m getflix
```

Then open <http://127.0.0.1:5000>.

> Run qBittorrent behind a VPN. The [Docker](#docker) section below starts a
> complete stack (Getflix + qBittorrent with an IP leak killswitch) with a
> single command.

## Docker

The repository ships a `docker-compose.yml` that runs three services:

| Service | Image | Purpose |
| --- | --- | --- |
| `getflix` | built from `Dockerfile` | The web interface, served by gunicorn on port 5000 |
| `qbittorrent` | `binhex/arch-qbittorrentvpn` | qBittorrent behind an OpenVPN or WireGuard tunnel with an iptables killswitch |
| `qbittorrent-init` | `python:3.12-alpine` | One shot helper that sets the WebUI password and installs the search plugins |

```
browser ──▶ getflix :5000 ──▶ qbittorrent :8080 ──▶ VPN tunnel ──▶ internet
```

### Quick start

```bash
cp .env.example .env
# 1. put the .ovpn or .conf file of your provider into config/qbittorrent/
# 2. set the VPN details and a WebUI password in .env
make docker-up
```

`make docker-up` is a wrapper around `docker compose up -d --build`. Getflix
is then available on <http://127.0.0.1:5000> and the qBittorrent WebUI on
<http://127.0.0.1:8080>.

To try the stack without a VPN, set `VPN_ENABLED=no` in `.env`. The
killswitch is not active in that case, so only use it on a trusted network.

### Setting up the VPN

The container expects the configuration file of your provider in the mounted
volume. Create the directory before the first start, otherwise Docker creates
it as `root` and you cannot copy the file into it:

```bash
mkdir -p config/qbittorrent/openvpn    # OpenVPN
mkdir -p config/qbittorrent/wireguard  # WireGuard
```

| Client | Location on the host | Notes |
| --- | --- | --- |
| OpenVPN | `config/qbittorrent/openvpn/*.ovpn` | The first `.ovpn` file that is found is used |
| WireGuard | `config/qbittorrent/wireguard/*.conf` | The file is renamed to `wg0.conf` on startup |

**Custom OpenVPN**

1. Download the `.ovpn` file of your provider and put it into
   `config/qbittorrent/openvpn/`, for example
   `config/qbittorrent/openvpn/nl.privacy.network.ovpn`.
2. Set the connection details in `.env`:

   ```ini
   VPN_ENABLED=yes
   VPN_CLIENT=openvpn
   VPN_PROV=custom
   VPN_USER=your-user
   VPN_PASS=your-password
   VPN_REMOTE_SERVER=nl.privacy.network
   ```

   `VPN_USER` and `VPN_PASS` are optional, they are only needed when the
   `.ovpn` file asks for a user name and a password. `VPN_REMOTE_SERVER` is
   the host name of the VPN server, it is used to resolve the address of the
   tunnel before the connection is up.

**WireGuard**

1. Put the `.conf` file of your provider into `config/qbittorrent/wireguard/`.
2. Set `VPN_CLIENT=wireguard` and `VPN_PROV=custom` in `.env`. The endpoint of
   the tunnel is read from the `Endpoint` line of the configuration file.

**Provider presets**

`VPN_PROV=pia` and `VPN_PROV=protonvpn` let the container build the
connection itself, you only have to provide `VPN_USER` and `VPN_PASS`. PIA
also generates a WireGuard configuration on the fly and can request a port
forwarding (`STRICT_PORT_FORWARD=yes`).

WireGuard needs the `NET_ADMIN` capability, which is already enabled in
`docker-compose.yml`. If the container cannot create `/dev/net/tun` itself,
uncomment the `devices` section of the `qbittorrent` service.

> **`LAN_NETWORK` is mandatory** while the VPN is enabled. It must contain the
> subnet of your LAN (for example `192.168.1.0/24`), not the address of a
> single host. `binhex/arch-qbittorrentvpn` exits with
> `[crit] LAN_NETWORK not defined (via -e LAN_NETWORK), exiting...` when it is
> missing. Find the subnet on the docker host with:
>
> ```bash
> ip -o -4 addr show scope global
> ```

### The killswitch

The `binhex/arch-qbittorrentvpn` image sets the default policy of the
`INPUT`, `FORWARD` and `OUTPUT` chains to `DROP` (for IPv4 and IPv6) before
the tunnel is started and only adds `ACCEPT` rules for the tunnel interface
and the Docker network. Every packet that is not routed through the tunnel is
dropped, so the real IP address of the host never leaks - not when the tunnel
breaks, not while qBittorrent is connecting and not for DNS requests either.

The health check of the container verifies that the tunnel is up (the address
of the tunnel is written to `/tmp/getvpnip` by the start script) before it
reports the service as healthy.

### Ports and security

By default both published ports are bound to `127.0.0.1` and are therefore
only reachable from the host:

```ini
QBITTORRENT_WEBUI_BIND=127.0.0.1
GETFLIX_BIND_ADDRESS=127.0.0.1
```

Set `LAN_NETWORK` to the subnet of your local network (for example
`192.168.1.0/24`) to allow the WebUI to be reached from there, and change
the bind address to `0.0.0.0` at the same time. Do not expose the WebUI to
the internet, and change the default password.

Requests that come from the Docker network itself
(`GETFLIX_DOCKER_SUBNET`, `172.28.0.0/16` by default) may skip the
authentication. Set `QBITTORRENT_AUTH_SUBNET=` (empty) to require the
password everywhere.

### Search plugins

qBittorrent 5.x is shipped without search engines. The `qbittorrent-init`
service downloads the official plugins into the profile
(`config/qbittorrent/qBittorrent/data/nova3/engines`) before qBittorrent
starts, so a search works right after the first start. When the plugins have
to be installed manually, use *Tools → Plugins* in the WebUI.

To reinstall them later:

```bash
make docker-seed
```

### Docker configuration

All variables live in `.env`, the full list is documented in `.env.example`.

| Variable | Default | Description |
| --- | --- | --- |
| `VPN_ENABLED` | `yes` | `no` runs qBittorrent without a tunnel |
| `VPN_CLIENT` | `openvpn` | `openvpn` or `wireguard` |
| `VPN_PROV` | `custom` | `pia`, `airvpn`, `protonvpn` or `custom` |
| `VPN_USER` / `VPN_PASS` | – | Credentials of the VPN account |
| `VPN_REMOTE_SERVER` | – | Host name of the VPN server |
| `VPN_OPTIONS` | – | Extra options for the OpenVPN client |
| `STRICT_PORT_FORWARD` | `no` | Ask PIA/ProtonVPN for a forwarded port |
| `NAME_SERVERS` | `1.1.1.1,1.0.0.1` | Name servers used inside the container |
| `LAN_NETWORK` | `192.168.1.0/24` | **Required while the VPN is enabled**, the subnet of your LAN |
| `QBITTORRENT_USERNAME` / `QBITTORRENT_PASSWORD` | `admin` / `adminadmin` | WebUI credentials |
| `QBITTORRENT_WEBUI_BIND` / `QBITTORRENT_WEBUI_PORT` | `127.0.0.1` / `8080` | Published WebUI port |
| `GETFLIX_BIND_ADDRESS` / `GETFLIX_PORT` | `127.0.0.1` / `5000` | Published Getflix port |
| `PUID` / `PGID` | `1000` / `1000` | Owner of the files in the volumes |
| `GETFLIX_DOCKER_SUBNET` | `172.28.0.0/16` | Subnet of the compose network |
| `QBITTORRENT_AUTH_SUBNET` | the compose subnet | Range that may skip the authentication |

### Makefile targets

```bash
make docker-build    # build the Getflix image
make docker-up       # build and start the whole stack
make docker-down     # stop the stack
make docker-logs     # follow the logs of every service
make docker-seed     # re-apply the password and install the search plugins
make docker-config   # show the resolved compose configuration
make docker-shell    # shell inside the running Getflix container
```

### Troubleshooting

**The WebUI answers with `401 Unauthorized`.** The password in `.env` and the
password in the qBittorrent configuration do not match. The `qbittorrent-init`
service writes the password into the configuration on every start, so run
`make docker-seed` and restart the container afterwards. The log of the
container shows the reason:

```bash
docker compose logs qbittorrent | grep -i "login"
```

**The WebUI answers with `403 Forbidden` or a host header error.** qBittorrent
5.3 validates the `Host` header of the request against the port it listens
on. The init service therefore writes `HostHeaderValidation=false` into the
configuration, a request that is not made through the compose network (for
example with `curl http://localhost:8080`) is only accepted because of it.

**A search returns no results.** The search plugins are missing. Check
`config/qbittorrent/qBittorrent/data/nova3/engines` and install them with
`make docker-seed` or through *Tools → Plugins* of the WebUI. The plugins are
downloaded by the `qbittorrent-init` container, which runs once before
qBittorrent starts, so it needs internet access on the first start.

**The container restarts in a loop.** Look at the output of the VPN client:

```bash
docker compose logs qbittorrent | head -50
```

The most common reasons are a missing `.ovpn` file, wrong credentials and a
`VPN_REMOTE_SERVER` that cannot be resolved. Two messages are worth knowing:

- `[crit] LAN_NETWORK not defined (via -e LAN_NETWORK), exiting...` - set
  `LAN_NETWORK` in `.env` to the subnet of your LAN, for example
  `192.168.1.0/24` (see [Setting up the VPN](#setting-up-the-vpn)).
- `AUTH: Received control message: AUTH_FAILED` - the VPN provider rejected
  `VPN_USER` and `VPN_PASS`. ProtonVPN, for example, wants the
  *OpenVPN/IKEv2 username and password* from the account dashboard, not the
  login of the account itself.

**Getflix says "Cannot reach qBittorrent" but the containers are up.** With
`VPN_ENABLED=yes` the image only starts qBittorrent after the tunnel is up,
so the WebUI is unavailable while the VPN is still connecting (or failing).
Check the tunnel first:

```bash
docker compose logs qbittorrent | grep -E "AUTH_FAILED|Initialization Sequence|getvpnip"
```

If the tunnel is connected the WebUI is reachable from the Getflix container
regardless of `LAN_NETWORK`, the compose network is always allowed.

**Nothing is downloaded.** The volume `./downloads` is mounted as `/data` in
both containers, the default save path is `/data/getflix`. Make sure the
directory is writable by `PUID`/`PGID`.

## How it works

```
browser ──htmx──▶ Flask ──requests──▶ qBittorrent WebUI
```

| Route | Purpose |
| --- | --- |
| `GET /` | The search page |
| `GET /search?q=…` | Runs a search and renders the result list |
| `POST /torrents` | Adds a torrent to qBittorrent |
| `GET /progress/<id>` | Progress of the torrent behind a search result |
| `GET /downloads` | The Downloads panel (active + completed) |
| `POST /torrents/<hash>/{pause,resume,seed}` | Torrent controls |
| `DELETE /torrents/<hash>` | Removes a torrent |
| `GET /api/status` | JSON status, handy for debugging |
| `GET /healthz` | Health check of the container |

The HTMX fragments live in `src/getflix/templates/partials/`.

### qBittorrent compatibility

Both search APIs are supported:

- **qBittorrent 5.x** uses the asynchronous `search/start` → `search/status` →
  `search/results` endpoints.
- **qBittorrent 4.x** uses the synchronous `search/index` endpoint.

The one day seeding limit uses `torrents/setShareLimits`, which was introduced
in qBittorrent 5.0. On qBittorrent 4.x Getflix falls back to the legacy
`setShareRatio` endpoint, which only understands ratio limits.

## Development

```bash
uv sync --all-groups
uv run pytest          # tests
uv run ruff check .    # lint
```

The same commands are available through the `Makefile`:

```bash
make install    # uv sync --all-groups
make run        # uv run getflix
make check      # lint + test
```

## License

MIT
