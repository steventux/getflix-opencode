# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build stage - resolve the dependencies into a self contained virtualenv
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# uv is the package manager used by this project (see uv.lock).
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install the dependencies first, they only change when the lock file does.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY README.md ./README.md
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.source="https://github.com/steventux/getflix" \
      org.opencontainers.description="Getflix - fuzzy search for torrents on a local qBittorrent instance"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:${PATH}" \
    GETFLIX_HOST=0.0.0.0 \
    GETFLIX_PORT=5000 \
    GETFLIX_WORKERS=2 \
    GETFLIX_THREADS=4 \
    GETFLIX_TIMEOUT=120

# The application runs as an unprivileged user.
RUN groupadd --system --gid 1000 getflix \
 && useradd --system --uid 1000 --gid 1000 --home-dir /app --shell /usr/sbin/nologin getflix

WORKDIR /app

COPY --from=builder --chown=getflix:getflix /app/.venv /app/.venv
COPY --chown=getflix:getflix src /app/src
COPY --chown=getflix:getflix docker/entrypoint.sh /usr/local/bin/getflix-entrypoint.sh

# gunicorn creates its control socket in the working directory.
RUN chown getflix:getflix /app

USER getflix

EXPOSE 5000

# The endpoint answers as long as the Flask application is up, it does not
# depend on qBittorrent being reachable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('GETFLIX_PORT', '5000') + '/healthz', timeout=4).status == 200 else 1)"

# A search may take up to GETFLIX_SEARCH_TIMEOUT seconds, therefore the
# gunicorn timeout has to be larger than that.
ENTRYPOINT ["/usr/local/bin/getflix-entrypoint.sh"]
