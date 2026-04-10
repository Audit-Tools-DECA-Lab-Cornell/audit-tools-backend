#!/bin/bash
# Activate backend environment and show useful info

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Check if .venv exists
if [ ! -d "$PROJECT_ROOT/.venv" ]; then
    echo "Creating virtual environment..."
    python -m venv "$PROJECT_ROOT/.venv"
    source "$PROJECT_ROOT/.venv/bin/activate"
    python -m pip install --upgrade pip setuptools wheel
    python -m pip install -r "$PROJECT_ROOT/requirements.txt"
else
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

echo ""
echo "✓ Backend environment activated"
echo ""
echo "Quick commands:"
echo "  - Start API:  uvicorn app.main:app --reload"
echo "  - Run tests:  pytest"
echo "  - Lint code:  ruff check ."
echo ""
echo "API endpoints (when running):"
echo "  - Health: http://127.0.0.1:8000/health"
echo "  - YEE Auth: http://127.0.0.1:8000/yee/auth/login"
echo "  - Playspace Auth: http://127.0.0.1:8000/playspace/auth/login"
echo ""
