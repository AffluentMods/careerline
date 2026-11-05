#!/usr/bin/env sh
# SPDX-License-Identifier: Apache-2.0
# Start CareerLine locally (macOS/Linux).
# Usage: ./start.sh [--dev] [--rebuild] [--demo] [--restore FILE] [--verify-backup FILE]
#                   [--rotate-key] [--rekey] [--key-status]
set -eu
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
(cd backend && uv sync --frozen --python 3.12)
[ -d frontend/node_modules ] || (cd frontend && npm ci)
exec backend/.venv/bin/python backend/scripts/run_local.py "$@"
