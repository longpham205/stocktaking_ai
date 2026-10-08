#!/usr/bin/env bash
# The web POS with the REAL recognition pipeline on this machine's GPU (Windows: double-click
# run_real.bat). The API runs on the host, in a Python environment that has both the ML stack and
# the web libraries; Postgres stays in Docker; the frontend is Vite on the host.
#
#   ./scripts/run_real.sh       this machine only (http://localhost:5173)
#   ./scripts/run_real.sh lan   also reachable from a phone on the same network (run_real_phone.bat)
#   ./scripts/run_real.sh tunnel  a public https:// address through a Cloudflare quick tunnel: phones on
#                               any network (their own 4G), and the in-page camera works (run_real_tunnel.bat).
#                               Needs cloudflared: on PATH, CLOUDFLARED=<path>, or tools/cloudflared.exe.
#
# The window stays open while it runs. Ctrl+C stops the API and the frontend (Postgres keeps running).
#
# Which Python: ML_PYTHON, or ../stocktaking_ai_mini/venv next to this repo. It needs torch with
# CUDA plus: fastapi uvicorn asyncpg "psycopg[binary]" alembic pyjwt.
# Other settings: PIPELINE_CONFIG (default configs/config.yaml), NO_OPEN=1 keeps the browser closed.
# It does not create accounts or data: the database is used as it is.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
MODE="${1:-}"

export RECOGNIZER=local
export PIPELINE_CONFIG="${PIPELINE_CONFIG:-configs/config.yaml}"
export PYTHONIOENCODING=utf-8
API_LOG="$ROOT/backups/run_real_api.log"

step() { printf '\n==== %s ====\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }

step "1/5 tools"
PY="${ML_PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in "$ROOT/../stocktaking_ai_mini/venv/Scripts/python.exe" "$ROOT/../stocktaking_ai_mini/venv/bin/python"; do
    [ -x "$candidate" ] && PY="$candidate" && break
  done
fi
[ -n "$PY" ] && [ -x "$PY" ] || fail "no Python with the ML stack: set ML_PYTHON to its python executable"
"$PY" - <<'EOF' || fail "this Python cannot run the real pipeline (see the line above)"
import importlib.util, sys
missing = [m for m in ("torch", "faiss", "fastapi", "uvicorn", "asyncpg", "psycopg", "alembic", "jwt") if importlib.util.find_spec(m) is None]
if missing:
    sys.exit("missing modules: " + ", ".join(missing))
import torch
if not torch.cuda.is_available():
    sys.exit("torch does not see a CUDA GPU")
print("ok: python", sys.version.split()[0], "torch", torch.__version__, "on", torch.cuda.get_device_name(0))
EOF
command -v docker > /dev/null 2>&1 || fail "'docker' not found on PATH"
docker info > /dev/null 2>&1 || fail "Docker is not running: start Docker Desktop, then run this again"
[ -f .env ] || fail ".env is missing: run 'make setup' once (docs/instruct_dev.md, section 2)"
[ -x frontend/node_modules/.bin/vite ] || fail "frontend/node_modules is missing: run 'cd frontend && pnpm install --frozen-lockfile'"

step "2/5 postgres up; the Docker api and web are stopped (same ports)"
docker compose up -d --wait postgres
docker compose stop api web > /dev/null 2>&1 || true

# localhost, not 127.0.0.1: Vite listens on the IPv6 loopback only
for port in 8000 5173; do
  if curl -s -m 2 -o /dev/null "http://localhost:$port/"; then
    fail "port $port is still in use: another copy of this script (or another server) is running; close it first"
  fi
done

CF=""
if [ "$MODE" = "tunnel" ]; then
  CF="${CLOUDFLARED:-}"
  [ -n "$CF" ] || CF="$(command -v cloudflared || true)"
  [ -n "$CF" ] || { [ -x "$ROOT/tools/cloudflared.exe" ] && CF="$ROOT/tools/cloudflared.exe"; }
  [ -n "$CF" ] || fail "tunnel mode needs cloudflared: put it on PATH, in tools/cloudflared.exe, or set CLOUDFLARED"
  echo "ok: $("$CF" --version 2>&1 | head -n 1)"
fi

step "3/5 migrations"
(cd backend && "$PY" -m alembic upgrade head)

step "4/5 API with the real pipeline (loading the models takes a minute or two)"
mkdir -p backups
(cd backend && exec "$PY" -m uvicorn entrypoints.api:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log) > "$API_LOG" 2>&1 &
API_PID=$!
TUNNEL_PID=""
stop_api() {
  kill "$API_PID" > /dev/null 2>&1 || true
  [ -z "$TUNNEL_PID" ] || kill "$TUNNEL_PID" > /dev/null 2>&1 || true
  printf '\nStopped. Back to the Docker version (fake recognizer): docker compose up -d --wait api web\n'
}
trap stop_api EXIT

ready=""
for _ in $(seq 1 120); do
  kill -0 "$API_PID" > /dev/null 2>&1 || break
  if curl -s -m 3 http://127.0.0.1:8000/api/health 2> /dev/null | grep -q '"recognizer":"local"'; then ready=1; break; fi
  sleep 3
done
if [ -z "$ready" ]; then
  tail -n 25 "$API_LOG" >&2 || true
  fail "the API did not become ready (log: $API_LOG)"
fi
echo "ok: $(curl -s -m 3 http://127.0.0.1:8000/api/health)"

step "5/5 frontend -> http://localhost:5173   (Ctrl+C stops everything)"
echo "API log: $API_LOG"
if [ -z "${NO_OPEN:-}" ]; then
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) (sleep 4; cmd.exe //c start "" "http://localhost:5173" > /dev/null 2>&1 || true) & ;;
    Darwin) (sleep 4; open "http://localhost:5173" > /dev/null 2>&1 || true) & ;;
    *) (sleep 4; xdg-open "http://localhost:5173" > /dev/null 2>&1 || true) & ;;
  esac
fi
cd frontend
if [ "$MODE" = "lan" ]; then
  # every interface: a phone on the same Wi-Fi or hotspot opens the "Network" address Vite prints.
  # The API itself stays on 127.0.0.1; the phone reaches it through this server's /api proxy.
  echo "PHONE: open the http://<Network address>:5173 printed below (not localhost). Windows may ask to allow Node.js through the firewall: allow it."
  ./node_modules/.bin/vite --port 5173 --strictPort --host 0.0.0.0
elif [ "$MODE" = "tunnel" ]; then
  # Cloudflare quick tunnel (no account): a random https://<words>.trycloudflare.com address that
  # forwards to this Vite server; Vite must accept that host name. Anyone with the address reaches the
  # login page: reset the passwords used during the session afterwards.
  TUNNEL_LOG="$ROOT/backups/run_real_tunnel.log"
  "$CF" tunnel --no-autoupdate --url http://127.0.0.1:5173 > "$TUNNEL_LOG" 2>&1 &
  TUNNEL_PID=$!
  (
    for _ in $(seq 1 60); do
      # `|| true`: no address yet makes grep fail, and set -e would end this loop at once
      url="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2> /dev/null | head -n 1 || true)"
      if [ -n "$url" ]; then
        printf '\n==== PHONE (any network): open %s ====\n\n' "$url"
        # a QR code of the address for the phones to scan; the address changes at every start
        QR="$ROOT/backups/tunnel_qr.png"
        if (cd "$ROOT/backend" && "$PY" scripts/make_qr.py "$url" --out "$QR") && [ -z "${NO_OPEN:-}" ]; then
          case "$(uname -s)" in
            MINGW* | MSYS* | CYGWIN*) cmd.exe //c start "" "$(cygpath -w "$QR")" > /dev/null 2>&1 || true ;;
            Darwin) open "$QR" > /dev/null 2>&1 || true ;;
            *) xdg-open "$QR" > /dev/null 2>&1 || true ;;
          esac
        fi
        exit 0
      fi
      sleep 1
    done
    echo "the tunnel address did not appear: see $TUNNEL_LOG" >&2
  ) &
  # IPv4 loopback: cloudflared dials 127.0.0.1, and Vite alone would listen on ::1 only
  VITE_ALLOWED_HOSTS=".trycloudflare.com" ./node_modules/.bin/vite --port 5173 --strictPort --host 127.0.0.1
else
  ./node_modules/.bin/vite --port 5173 --strictPort
fi
