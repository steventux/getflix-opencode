#!/bin/sh
# Register a vlc: URL handler so a click on a finished download opens VLC.
#
# Getflix links completed downloads to vlc:http://<host>/play/<hash>. The
# browser hands that scheme to the desktop, which runs the helper installed
# here. No root, no browser extension.
#
#   ./scripts/install-vlc-handler.sh              install
#   ./scripts/install-vlc-handler.sh --uninstall  remove it again
set -eu

DESKTOP_ID="getflix-vlc.desktop"
BIN_DIR="${HOME}/.local/bin"
HELPER="${BIN_DIR}/getflix-vlc-open"
APPLICATIONS_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
DESKTOP_PATH="${APPLICATIONS_DIR}/${DESKTOP_ID}"
MIMEAPPS="${XDG_CONFIG_HOME:-${HOME}/.config}/mimeapps.list"

usage() {
    cat <<'EOF'
usage: install-vlc-handler.sh [--uninstall]

Registers the vlc: URL scheme for the current user, so that clicking a
completed download in Getflix opens it in VLC.

  --uninstall   remove the helper and the desktop entry again
  -h, --help    show this help
EOF
}

uninstall() {
    rm -f "${HELPER}" "${DESKTOP_PATH}"
    if [ -f "${MIMEAPPS}" ]; then
        # Drop our default so the scheme falls back to "nothing" again.
        sed -i.bak '/^x-scheme-handler\/vlc=/d' "${MIMEAPPS}"
        rm -f "${MIMEAPPS}.bak"
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
    fi
    echo "Removed the vlc: handler."
}

install() {
    for tool in vlc xdg-mime; do
        if ! command -v "${tool}" >/dev/null 2>&1; then
            echo "${tool} is required but was not found in PATH." >&2
            exit 1
        fi
    done

    mkdir -p "${BIN_DIR}" "${APPLICATIONS_DIR}"

    cat > "${HELPER}" <<'EOF'
#!/bin/sh
# Installed by Getflix: open vlc:http://... links in VLC.
set -eu
if [ "$#" -lt 1 ]; then
    echo "usage: getflix-vlc-open vlc:<url>" >&2
    exit 2
fi
url=$1
# Canonical form is vlc:http://..., the double slash form is tolerated.
url=${url#vlc://}
url=${url#vlc:}
exec vlc "$url"
EOF
    chmod 755 "${HELPER}"

    cat > "${DESKTOP_PATH}" <<EOF
[Desktop Entry]
Type=Application
Name=Getflix VLC Links
Comment=Open vlc: links from Getflix in VLC
Exec="${HELPER}" %u
Icon=vlc
Terminal=false
NoDisplay=true
MimeType=x-scheme-handler/vlc;
Categories=AudioVideo;Player;
StartupNotify=false
EOF

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
    fi
    xdg-mime default "${DESKTOP_ID}" x-scheme-handler/vlc

    handler=$(xdg-mime query default x-scheme-handler/vlc)
    if [ "${handler}" != "${DESKTOP_ID}" ]; then
        echo "Could not register the handler (got '${handler}')." >&2
        exit 1
    fi

    echo "vlc: links now open in ${handler}."
    echo "Click the name of a completed download in Getflix to try it."
}

case "${1:-}" in
    "") install ;;
    --uninstall) uninstall ;;
    -h | --help) usage ;;
    *)
        usage >&2
        exit 2
        ;;
esac
