#!/bin/bash
# Health check for the qBittorrent container.
#
# It verifies that
#   * the VPN tunnel is up (only when the VPN is enabled) and
#   * the qBittorrent WebUI is listening.
#
# The script is mounted into the container by docker-compose.yml, it needs
# nothing but bash and the tools of the image itself.

set -u

# The start script writes the address of the tunnel into this file as soon
# as the VPN is connected.
if [[ "${VPN_ENABLED:-yes}" == "yes" ]]; then

	if [[ ! -s /tmp/getvpnip ]]; then
		echo "VPN tunnel is not up yet"
		exit 1
	fi

fi

port="${WEBUI_PORT:-8080}"

if ! timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/${port}" 2>/dev/null; then
	echo "qBittorrent WebUI is not listening on port ${port}"
	exit 1
fi

exit 0
