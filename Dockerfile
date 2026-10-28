# syntax=docker/dockerfile:1

# --- Frontend build -----------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite writes the bundle to ../backend/overlaparr/static
RUN mkdir -p /src/backend/overlaparr && npm run build

# --- Runtime --------------------------------------------------------------------
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    OVERLAPARR_CONFIG_DIR=/config

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/alembic.ini backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/overlaparr ./overlaparr
RUN uv sync --frozen --no-dev
COPY --from=web /src/backend/overlaparr/static ./overlaparr/static
COPY --chmod=0755 docker/entrypoint.sh /entrypoint.sh

EXPOSE 8686
VOLUME /config
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('OVERLAPARR_PORT','8686')}/health\")"]
ENTRYPOINT ["/entrypoint.sh"]
