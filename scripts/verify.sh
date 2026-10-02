#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"
.venv/bin/ruff check .
.venv/bin/pytest -q -m "not live"

cd "$ROOT_DIR/frontend"
npm run lint
npm test
npm run build

cd "$ROOT_DIR"
git diff --check
