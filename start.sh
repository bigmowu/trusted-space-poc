#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -x .venv/bin/python ]]; then
  PYTHON_BIN=.venv/bin/python
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
mkdir -p logs

echo "可信数据空间: http://${HOST}:${PORT}"
echo "业务事件: $(pwd)/logs/events.log"
echo "执行细节: $(pwd)/logs/trace.log"
echo "HTTP 访问: $(pwd)/logs/http.log"
if [[ "${RELOAD:-0}" == "1" ]]; then
  exec "${PYTHON_BIN}" -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" --reload
else
  exec "${PYTHON_BIN}" -m uvicorn app.main:app --host "${HOST}" --port "${PORT}"
fi

