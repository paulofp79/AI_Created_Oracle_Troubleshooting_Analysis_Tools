#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apps=(
  "Dashboard|8079|exaweb.log"
  "ECS Analysis|8501|streamlit_ecs.log"
  "AWR Repository Explorer|8502|streamlit_awr.log"
  "ExaWatcher Iostat Analyzer|8503|streamlit_iostat.log"
  "ExaWatcher Netstat Analyzer|8504|streamlit_netstat.log"
)

if ! command -v lsof >/dev/null 2>&1; then
  echo "Cannot check app ports: lsof is not installed." >&2
  exit 1
fi

printf '%-31s %-8s %-12s %s\n' "Application" "Port" "Status" "Log"
printf '%-31s %-8s %-12s %s\n' "-----------" "----" "------" "---"

for app in "${apps[@]}"; do
  IFS='|' read -r name port logfile <<< "${app}"
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    state="STARTED"
    pid_list="$(tr '\n' ',' <<< "${pids}" | sed 's/,$//')"
    state="${state} (PID ${pid_list})"
  else
    state="STOPPED"
  fi
  printf '%-31s %-8s %-24s %s\n' "${name}" "${port}" "${state}" "${ROOT_DIR}/${logfile}"
done
