#!/usr/bin/env sh
# First run: builds a private Python environment, installs the requirements and writes config.toml.
# After that it just starts the farmer. Extra arguments are passed on (for example: ./start.sh --plain).
cd "$(dirname "$0")" || exit 1

VENV="${OWO_FARMER_VENV:-.venv}"
PY="$VENV/bin/python"

if [ -x "$PY" ] && "$PY" -c "import discord, rich, aiohttp" >/dev/null 2>&1; then
    :
else
    if [ -x "$PY" ] && "$PY" -m pip --version >/dev/null 2>&1; then
        :
    else
        echo "Setting up, first run only. This can take a few minutes..."
        rm -rf "$VENV"
        python3 -m venv "$VENV" || { echo "Need Python 3.10+ with the venv module."; exit 1; }
    fi
    "$PY" -m pip install --disable-pip-version-check -r requirements.txt || exit 1
    echo "Setup finished."
fi

if [ ! -f "config.toml" ]; then
    "$PY" owo_farmer.py --init
    echo
    echo "Open config.toml, set farm.channel_id, then run ./start.sh again."
    exit 0
fi

exec "$PY" owo_farmer.py "$@"
