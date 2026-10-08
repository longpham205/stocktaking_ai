#!/usr/bin/env bash
# The whole web POS in Docker (Windows: double-click run_docker.bat): Postgres, migrations, API and
# frontend. Run scripts/setup.sh once before (catalog, accounts, and the weights for gpu).
#
#   ./scripts/run_docker.sh         demo recognizer (RECOGNIZER=fake: no model, any machine)
#   ./scripts/run_docker.sh gpu     real recognition on an NVIDIA GPU inside Docker: a large image
#                                   (~10 GB) built the first time; Docker Desktop needs the WSL2 backend
#   ./scripts/run_docker.sh stop    stop the containers (the database is kept)
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n==== %s ====\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }

command -v docker > /dev/null 2>&1 || fail "Docker is not installed: run scripts/setup.sh (setup.bat) first"
docker info > /dev/null 2>&1 || fail "Docker is not running: start Docker Desktop, then run this again"
[ -f .env ] || fail ".env is missing: run scripts/setup.sh (setup.bat) once"

COMPOSE=(docker compose)
case "${1:-}" in
  "") ;;
  gpu) COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.gpu.yml)
       [ -f backend/weights/detector/checkpoint_best_ema.pth ] || fail "the weights are missing: run scripts/setup.sh on this machine first" ;;
  stop) docker compose down; exit 0 ;;
  *) fail "unknown option: $1 (use gpu, stop or nothing)" ;;
esac

step "1/2 build and start postgres, migrate, api, web (the first build takes a few minutes; gpu: much longer)"
# the host-run API (run_real) would hold the same ports
for port in 8000 5173; do
  if curl -s -m 2 -o /dev/null "http://localhost:$port/" && ! docker compose ps --services --status running | grep -qx "$([ "$port" = 8000 ] && echo api || echo web)"; then
    fail "port $port is in use by another program (scripts/run_real.bat?): close it first"
  fi
done
"${COMPOSE[@]}" up --build -d --wait

step "2/2 ready -> http://localhost:5173"
curl -s -m 5 http://localhost:8000/api/health && echo
echo "Stop: scripts/run_docker.sh stop (or docker compose down)."
if [ -z "${NO_OPEN:-}" ]; then
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) cmd.exe //c start "" "http://localhost:5173" > /dev/null 2>&1 || true ;;
    Darwin) open "http://localhost:5173" > /dev/null 2>&1 || true ;;
    *) xdg-open "http://localhost:5173" > /dev/null 2>&1 || true ;;
  esac
fi
