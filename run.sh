#!/usr/bin/env bash
# Restart the Portfolio Optimizer app (kill existing uvicorn, then start backend).

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
APP_DIR="$PROJECT_ROOT/app"

echo "Stopping existing app..."
pkill -f "uvicorn.*backend.main:app" 2>/dev/null || true
sleep 2

echo "Rebuilding frontend..."
cd "$PROJECT_ROOT/app/frontend" && npm run build
cd "$PROJECT_ROOT"

echo "Starting app at http://0.0.0.0:8000 ..."
echo "Tip: To get a new session ID, refresh the browser (F5) or click 'New session' in the sidebar."
cd "$APP_DIR" && PYTHONPATH="$PROJECT_ROOT" uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
