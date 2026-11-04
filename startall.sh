#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# PYTHON_ENV_DIR may point to a venv directory, and PYTHON_BIN may point to
# its interpreter. The venv can use any supported Python version.
source "${ROOT_DIR}/scripts/python_env.sh"
VENV_PY="$(require_python_env "${ROOT_DIR}")"

if ! "${VENV_PY}" -c 'import streamlit, pandas' >/dev/null 2>&1; then
  echo "Python environment is missing one or more required packages: streamlit, pandas" >&2
  echo "Install them with: ${VENV_PY} -m pip install -r ${ROOT_DIR}/requirements.txt" >&2
  exit 1
fi

"${ROOT_DIR}/stopall.sh"

# Create a separate, fresh log for each port-bound application. This also
# leaves a useful log file when an app fails immediately during startup.
: > "${ROOT_DIR}/streamlit_exawatcher.log"
: > "${ROOT_DIR}/streamlit_awr_transfer.log"
: > "${ROOT_DIR}/exaweb.log"
: > "${ROOT_DIR}/streamlit_ecs.log"
: > "${ROOT_DIR}/streamlit_awr.log"
: > "${ROOT_DIR}/streamlit_iostat.log"
: > "${ROOT_DIR}/streamlit_netstat.log"

(
  cd "${ROOT_DIR}"
  nohup python3 -m http.server 8079 --bind 0.0.0.0 > "${ROOT_DIR}/exaweb.log" 2>&1 &
)
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/ECS_Analysis.py" --server.port 8501 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_ecs.log" 2>&1 &
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/AWR_Repository_Explorer.py" --server.port 8502 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_awr.log" 2>&1 &
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/Iostat_Analyzer.py" --server.port 8503 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_iostat.log" 2>&1 &
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/Netstat_Analyzer.py" --server.port 8504 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_netstat.log" 2>&1 &
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/ExaWatcher_Workbench.py" --server.port 8505 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_exawatcher.log" 2>&1 &
nohup "${VENV_PY}" -m streamlit run "${ROOT_DIR}/python/AWR_RAC_Transfer_Explorer.py" --server.port 8506 --server.address 0.0.0.0 > "${ROOT_DIR}/streamlit_awr_transfer.log" 2>&1 &

cat <<EOF
Started apps:
- Dashboard: http://localhost:8079/
- ECS Analysis: http://localhost:8501/
- AWR Repository Explorer: http://localhost:8502/
- ExaWatcher Iostat Analyzer: http://localhost:8503/
- ExaWatcher Netstat Analyzer: http://localhost:8504/
- ExaWatcher Analyzer Workbench: http://localhost:8505/
- AWR RAC Transfer Explorer: http://localhost:8506/

Logs:
- ${ROOT_DIR}/exaweb.log
- ${ROOT_DIR}/streamlit_ecs.log
- ${ROOT_DIR}/streamlit_awr.log
- ${ROOT_DIR}/streamlit_iostat.log
- ${ROOT_DIR}/streamlit_netstat.log
- ${ROOT_DIR}/streamlit_exawatcher.log
- ${ROOT_DIR}/streamlit_awr_transfer.log

Check listeners and log paths with:
  ${ROOT_DIR}/status.sh
EOF
