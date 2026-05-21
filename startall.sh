#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${VENV_PY}" ]]; then
  echo "Missing virtualenv python at ${VENV_PY}"
  echo "Create it first with: python3.12 -m venv .venv"
  exit 1
fi

"${ROOT_DIR}/stopall.sh"

(
  cd "${ROOT_DIR}"
  nohup python3 -m http.server 8079 --bind 0.0.0.0 > "${ROOT_DIR}/exaweb.log" 2>&1 &
)
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/ECS_Analysis.py" --server.port 8501 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_ecs.log" 2>&1 &
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/AWR_Repository_Explorer.py" --server.port 8502 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_awr.log" 2>&1 &
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/Iostat_Analyzer.py" --server.port 8503 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_iostat.log" 2>&1 &

cat <<EOF
Started apps:
- Dashboard: http://localhost:8079/
- ECS Analysis: http://localhost:8501/
- AWR Repository Explorer: http://localhost:8502/
- ExaWatcher Iostat Analyzer: http://localhost:8503/

Logs:
- ${ROOT_DIR}/exaweb.log
- ${ROOT_DIR}/streamlit_ecs.log
- ${ROOT_DIR}/streamlit_awr.log
- ${ROOT_DIR}/streamlit_iostat.log
EOF
