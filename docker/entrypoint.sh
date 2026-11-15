#!/bin/sh
# Entrypoint of the Getflix container.
#
# The application factory is passed to gunicorn as a single argument, a
# "sh -c" one liner would be parsed as a function definition by dash.
set -eu

exec gunicorn \
    --bind "${GETFLIX_HOST:-0.0.0.0}:${GETFLIX_PORT:-5000}" \
    --workers "${GETFLIX_WORKERS:-2}" \
    --threads "${GETFLIX_THREADS:-4}" \
    --timeout "${GETFLIX_TIMEOUT:-120}" \
    --access-logfile - \
    --error-logfile - \
    "getflix.app:create_app()"
