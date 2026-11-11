#!/usr/bin/env bash
# CS2-AimAudit launcher: creates the venv, installs packages, starts the app.
cd "$(dirname "$0")"

py_ok () {  # exit 0 if $1 exists and is Python >= 3.11
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

echo "[1/3] Checking Python (3.11+ required)..."
PY=""
for candidate in python3 python python3.14 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 && py_ok "$candidate"; then
    PY=$candidate
    break
  fi
done
if [ -z "$PY" ]; then
  echo "  Python 3.11+ was not found. Install it first"
  echo "  (Debian/Ubuntu: sudo apt install python3 python3-venv)."
  exit 1
fi

# A .venv left by an older AimAudit may run Python < 3.11 or be broken:
# recreate it with the current Python instead of dying inside pip.
if [ -d ".venv" ] && ! py_ok ".venv/bin/python"; then
  echo "  .venv is broken or runs an older Python; recreating it ..."
  rm -rf .venv
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "[1/3] Creating a virtual environment .venv ..."
  "$PY" -m venv .venv || {
    echo "  Could not create .venv (Debian/Ubuntu: sudo apt install python3-venv)."
    exit 1
  }
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/3] Installing packages, first run takes 1-3 minutes ..."
python -m pip install --quiet -r requirements.txt || {
  echo "  Package installation failed. Check your internet connection and try again."
  exit 1
}

# Streamlit asks for an email on its very first run and waits for Enter.
# An empty credentials file skips that prompt.
if [ ! -f "$HOME/.streamlit/credentials.toml" ]; then
  mkdir -p "$HOME/.streamlit"
  printf '[general]\nemail = ""\n' > "$HOME/.streamlit/credentials.toml"
fi

# First Streamlit launch asks for an email and waits for Enter - the
# terminal then looks frozen. An empty credentials.toml skips the prompt.
mkdir -p "$HOME/.streamlit" 2>/dev/null
[ -f "$HOME/.streamlit/credentials.toml" ] || : > "$HOME/.streamlit/credentials.toml" 2>/dev/null

echo "[3/3] Starting CS2-AimAudit, a browser tab will open ..."
python -m streamlit run app.py
