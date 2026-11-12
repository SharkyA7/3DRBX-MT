#!/usr/bin/env bash
# Dev server for 3DRBX-MT
# Starts python3 http.server on port 8090 from project root

PORT=8090
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if port is already in use
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "Error: Port $PORT is already in use."
  echo "Run 'lsof -ti:$PORT' to see the PID, or use a different port."
  exit 1
fi

echo "Starting dev server on http://localhost:$PORT"
echo "UV Checker: http://localhost:$PORT/uv-checker.html"
echo "Press Ctrl+C to stop"
echo ""

cd "$ROOT"
python3 -m http.server "$PORT"