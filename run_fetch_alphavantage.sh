#!/bin/sh
set -e

PYTHON_BIN="${PYTHON_BIN:-/usr/local/bin/python3}"
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/data_input/fetch_alphavantage_example.py"

# Example:
# ./run_fetch_alphavantage.sh --symbol TQQQ --insecure --apikey YOUR_API_KEY

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python not found at $PYTHON_BIN. Set PYTHON_BIN or update the script." >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_PATH" "$@"

