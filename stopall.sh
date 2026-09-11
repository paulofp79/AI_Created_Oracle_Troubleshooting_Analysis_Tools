#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Validate the same Python environment as startall.sh, even when stopall.sh
# is invoked directly. This prevents operating with an unknown environment.
source "${ROOT_DIR}/scripts/python_env.sh"
require_python_env "${ROOT_DIR}" >/dev/null

kill_port() {
  local port="$1"
  local pids

  pids="$(lsof -tiTCP:${port} -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    kill ${pids} || true
  fi
}

kill_port 8079
kill_port 8501
kill_port 8502
kill_port 8503
kill_port 8504

echo "Stopped app processes for ${ROOT_DIR}"
