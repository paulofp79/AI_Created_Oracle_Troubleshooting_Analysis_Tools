#!/usr/bin/env bash

# Resolve and validate the Python virtual environment used by the Streamlit
# applications. This file is sourced by startall.sh and stopall.sh.

require_python_env() {
  local root_dir="$1"
  local configured_bin="${PYTHON_BIN:-}"
  local env_dir="${PYTHON_ENV_DIR:-${VIRTUAL_ENV:-${root_dir}/.venv}}"
  local python_bin=""

  if [[ -n "${configured_bin}" ]]; then
    if [[ "${configured_bin}" == /* ]]; then
      python_bin="${configured_bin}"
    elif [[ "${configured_bin}" == */* ]]; then
      python_bin="${root_dir}/${configured_bin}"
    else
      python_bin="$(command -v "${configured_bin}" || true)"
    fi
  else
    if [[ "${env_dir}" != /* ]]; then
      env_dir="${root_dir}/${env_dir}"
    fi
    python_bin="${env_dir}/bin/python"
  fi

  if [[ -z "${python_bin}" || ! -x "${python_bin}" ]]; then
    cat >&2 <<EOF
Python virtual environment is not configured.
Expected interpreter: ${python_bin:-<not found>}

Create a virtual environment with the Python version installed on this host, for example:
  python3 -m venv "${root_dir}/.venv"
  "${root_dir}/.venv/bin/python" -m pip install -r "${root_dir}/requirements.txt"

Or use an existing environment:
  PYTHON_ENV_DIR=/path/to/venv ./startall.sh
  PYTHON_BIN=/path/to/venv/bin/python ./startall.sh
EOF
    return 1
  fi

  if ! "${python_bin}" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1; then
    echo "Python interpreter is not running from a virtual environment: ${python_bin}" >&2
    echo "Set PYTHON_ENV_DIR or PYTHON_BIN to a virtual environment before starting or stopping apps." >&2
    return 1
  fi

  printf '%s\n' "${python_bin}"
}
