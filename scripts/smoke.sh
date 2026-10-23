#!/usr/bin/env bash
# Headless smoke run against the in-memory fake server: no network, ~25 seconds. CI runs this.
# The intervals are shortened so the 30 s / 45 s flows execute several times within the run.
# RUN is the command prefix ("uv run" by default, "" inside an activated virtualenv).
set -euo pipefail
cd "$(dirname "$0")/.."

RUN="${RUN-uv run}"
PORT="${SMOKE_PORT:-50151}"
DURATION="${SMOKE_DURATION:-20s}"
LOG="$(mktemp)"

$RUN python -m vacancy_loadtest.fake_server --port "$PORT" --credentials credentials.example.json \
    --seed-vacancies 25 --latency-ms 10 >"$LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; rm -f "$LOG"' EXIT

$RUN python - "$PORT" <<'PY'
import socket, sys, time
port = int(sys.argv[1])
for _ in range(40):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        break
    except OSError:
        time.sleep(0.25)
else:
    sys.exit("fake server did not start")
PY

$RUN locust -f locustfile.py --headless --host "127.0.0.1:$PORT" \
    -u 3 -r 3 -t "$DURATION" --credentials-file credentials.example.json \
    --crud-interval 2 --list-interval 3 --list-page-limit 10 \
    --exit-code-on-error 1 --only-summary
