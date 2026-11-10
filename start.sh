#!/usr/bin/env bash
# CS2-AimAudit launcher: creates the venv, installs packages, starts the app.
cd "$(dirname "$0")"

echo "[1/3] Checking Python..."
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "  Python was not found. Install Python 3.10+"
  echo "  (Debian/Ubuntu: sudo apt install python3 python3-venv)."
  exit 1
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

echo "[3/3] Starting CS2-AimAudit, a browser tab will open ..."
python -m streamlit run app.py
