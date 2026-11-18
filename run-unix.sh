#!/usr/bin/env bash
# agproxy - macOS / Linux launcher
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[ERROR] Python 3.8+ is required but was not found." >&2
  exit 1
fi

exec "$PY" agproxy.py "$@"
