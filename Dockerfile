# SPDX-License-Identifier: Apache-2.0
# syntax=docker/dockerfile:1

# ---- 1. Build the frontend ----------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Install Python dependencies --------------------------------------------
FROM python:3.12-slim AS py
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/careerline ./careerline
RUN uv sync --frozen --no-dev --no-editable

# ---- 3. Runtime ------------------------------------------------------------------
FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home --home-dir /app careerline \
 && mkdir -p /backups \
 && chown 10001:10001 /backups
WORKDIR /app
COPY --from=py /app /app
COPY --from=web /web/dist /app/static
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAREERLINE_STATIC_DIR=/app/static \
    CAREERLINE_BACKUP_DIR=/backups
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/healthz', timeout=4)"]
# --forwarded-allow-ips: set to your reverse proxy's address if you put one in front.
CMD ["uvicorn", "careerline.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
