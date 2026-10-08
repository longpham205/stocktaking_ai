#!/usr/bin/env bash
# One-time setup of a fresh clone (Windows: double-click setup.bat). Safe to run again: every step
# skips what is already there, and an existing database is never replaced.
#
#   ./scripts/setup.sh          NVIDIA GPU found -> real recognition stack; none -> demo (fake recognizer)
#   ./scripts/setup.sh --cpu    the light install even on a machine with a GPU
#
# Steps: tools (Docker Desktop must be installed; uv and Node.js are installed when missing) ->
# .env with fresh secrets -> Python environment backend/.venv -> frontend packages -> released
# weights and data from the GitHub Release (~2.7 GB; the 610 MB data only with --cpu) -> Postgres in
# Docker, the released catalog when the database is new, migrations -> the accounts admin and staff
# when there is no account yet (their passwords are printed ONCE) -> cloudflared for the phone tunnel.
#
# Afterwards: scripts/run_real.bat (real recognition on the GPU) or scripts/run_docker.bat (no GPU).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

CPU_ONLY=""
for arg in "$@"; do
  case "$arg" in
    --cpu) CPU_ONLY=1 ;;
    *) echo "unknown option: $arg (use --cpu or nothing)" >&2; exit 2 ;;
  esac
done

step() { printf '\n==== %s ====\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }
WINDOWS=""
case "$(uname -s)" in MINGW* | MSYS* | CYGWIN*) WINDOWS=1 ;; esac

GPU=""
if [ -z "$CPU_ONLY" ] && command -v nvidia-smi > /dev/null 2>&1 && nvidia-smi -L > /dev/null 2>&1; then
  GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1)"
fi

step "1/8 tools"
command -v docker > /dev/null 2>&1 \
  || fail "Docker is not installed: install Docker Desktop (https://www.docker.com/products/docker-desktop/), start it once, then run this again"
if ! docker info > /dev/null 2>&1; then
  DESKTOP="/c/Program Files/Docker/Docker/Docker Desktop.exe"
  if [ -n "$WINDOWS" ] && [ -x "$DESKTOP" ]; then
    echo "starting Docker Desktop..."
    "$DESKTOP" > /dev/null 2>&1 &
    for _ in $(seq 1 90); do docker info > /dev/null 2>&1 && break; sleep 2; done
  fi
  docker info > /dev/null 2>&1 || fail "Docker is not running: start Docker Desktop, wait until it says it is running, then run this again"
fi
echo "ok: $(docker --version)"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv > /dev/null 2>&1; then
  echo "installing uv (the Python package manager, https://docs.astral.sh/uv/)..."
  if [ -n "$WINDOWS" ]; then
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  command -v uv > /dev/null 2>&1 || fail "uv was installed but is not on PATH: open a new terminal and run this again"
fi
echo "ok: $(uv --version)"

if [ -n "$WINDOWS" ]; then export PATH="/c/Program Files/nodejs:$PATH"; fi
if ! command -v node > /dev/null 2>&1; then
  if [ -n "$WINDOWS" ] && command -v winget > /dev/null 2>&1; then
    echo "installing Node.js LTS with winget (Windows may ask for permission)..."
    winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
  fi
  command -v node > /dev/null 2>&1 || fail "Node.js is not installed: install the LTS version from https://nodejs.org, then run this again"
fi
echo "ok: node $(node --version)"

step "2/8 .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi
for key in MEDIA_URL_SECRET JWT_SECRET; do
  if ! grep -q "^$key=..*" .env; then
    secret="$(openssl rand -hex 32 2> /dev/null || od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
    if grep -q "^$key=" .env; then
      sed -i.bak "s|^$key=.*|$key=$secret|" .env && rm -f .env.bak
    else
      printf '%s=%s\n' "$key" "$secret" >> .env
    fi
    echo ".env: generated $key"
  fi
done

step "3/8 Python environment (backend/.venv)"
if [ -n "$GPU" ]; then
  echo "NVIDIA GPU: $GPU -> real recognition stack (torch with CUDA, several GB the first time)"
  (cd backend && uv sync --extra ml)
  (cd backend && uv run --no-sync python -c "import torch; assert torch.cuda.is_available(), 'torch does not see the GPU'; print('ok: torch', torch.__version__, 'on', torch.cuda.get_device_name(0))") \
    || fail "torch cannot use the GPU: update the NVIDIA driver (CUDA 12.8 needs 570 or newer), or run with --cpu"
else
  echo "no NVIDIA GPU (or --cpu): light install, the web runs with the demo recognizer"
  (cd backend && uv sync)
fi

step "4/8 frontend packages"
PNPM="$(sed -n 's/.*"packageManager": *"\(pnpm@[^"]*\)".*/\1/p' frontend/package.json)"
[ -n "$PNPM" ] || fail "frontend/package.json has no packageManager entry"
(cd frontend && npx --yes "$PNPM" install --frozen-lockfile)

step "5/8 released weights and data (GitHub Release; kept in backups/assets/)"
if [ -n "$GPU" ]; then
  (cd backend && uv run --no-sync python scripts/fetch_assets.py)
else
  (cd backend && uv run --no-sync python scripts/fetch_assets.py --only data)
fi

step "6/8 database (Postgres in Docker, port 5437)"
docker compose up -d --wait postgres
psql_count() { docker compose exec -T postgres psql -U stocktaking -d stocktaking -tAc "$1" | tr -d '[:space:]'; }
if [ "$(psql_count "select count(*) from information_schema.tables where table_schema = 'public'")" = "0" ]; then
  [ -f backups/stocktaking_catalog.dump ] || fail "backups/stocktaking_catalog.dump is missing (step 5 should have written it)"
  docker compose exec -T postgres pg_restore -U stocktaking -d stocktaking --no-owner < backups/stocktaking_catalog.dump
  echo "new database: released catalog loaded (products, prices, recognition evidence; no account, no order)"
else
  echo "the database already has tables: kept as it is"
fi
(cd backend && uv run --no-sync alembic upgrade head)
echo "ok: $(psql_count "select count(*) from product where is_active") products on sale"

step "7/8 accounts"
if [ "$(psql_count "select count(*) from users")" = "0" ]; then
  echo "WRITE THESE PASSWORDS DOWN: they are shown only now."
  (cd backend && uv run --no-sync python -m entrypoints.reset_password admin --create --role admin)
  (cd backend && uv run --no-sync python -m entrypoints.reset_password staff --create --role staff)
  echo "Forgot one later: cd backend && uv run python -m entrypoints.reset_password <name>"
else
  echo "accounts already exist: kept as they are"
fi

step "8/8 cloudflared (public https link for phones, scripts/run_real_tunnel.bat)"
if [ -n "$WINDOWS" ]; then
  if [ -x tools/cloudflared.exe ]; then
    echo "ok: tools/cloudflared.exe is there"
  else
    mkdir -p tools
    curl -fL --progress-bar -o tools/cloudflared.exe.part \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe \
      && mv tools/cloudflared.exe.part tools/cloudflared.exe \
      && echo "ok: tools/cloudflared.exe" \
      || echo "could not download cloudflared (only the tunnel mode needs it): skipped"
  fi
else
  command -v cloudflared > /dev/null 2>&1 && echo "ok: cloudflared on PATH" \
    || echo "skipped: install cloudflared yourself if you want the tunnel mode"
fi

printf '\n==== SETUP DONE ====\n'
if [ -n "$GPU" ]; then
  echo "Start: scripts/run_real.bat (this machine), run_real_phone.bat (phones on the same Wi-Fi),"
  echo "       run_real_tunnel.bat (phones on any network). Then open http://localhost:5173"
else
  echo "Start: scripts/run_docker.bat (web in Docker, demo recognizer). Then open http://localhost:5173"
fi
echo "Log in with admin or staff and the password printed above."
