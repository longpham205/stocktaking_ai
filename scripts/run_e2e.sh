#!/usr/bin/env bash
# One command for the whole end-to-end check (Windows: double-click run_e2e.bat):
#
#   ./scripts/run_e2e.sh       start everything, seed the demo catalog, run the smoke test, open the browser
#   ./scripts/run_e2e.sh full  the same, after lint, type-check and both test suites
#
# What it does, in order: make setup -> make docker-up -> make seed-demo -> two test accounts
# (e2e_admin, e2e_staff) -> make check-env -> make smoke. It stops at the first step that fails.
#
# Defaults: RECOGNIZER=fake and PIPELINE_CONFIG=configs/config.demo.yaml (no model needed). Set
# either variable before calling to change it. NO_OPEN=1 keeps the browser closed.
#
# The two test accounts get a new random password at every run; it is used for the smoke test and
# never printed. Your own accounts are not touched. The smoke test pays one real order: it stays
# in the database and in the day's report.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-}"
export RECOGNIZER="${RECOGNIZER:-fake}"
export PIPELINE_CONFIG="${PIPELINE_CONFIG:-configs/config.demo.yaml}"
export PYTHONIOENCODING=utf-8

step() { printf '\n==== %s ====\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }

step "1/7 tools"
for tool in docker uv make openssl; do
  command -v "$tool" > /dev/null 2>&1 || fail "'$tool' not found on PATH (see docs/instruct_dev.md, section 1)"
done
docker info > /dev/null 2>&1 || fail "Docker is not running: start Docker Desktop, then run this again"
[ -d backend/data_demo/seed ] || fail "backend/data_demo/ is missing: copy it from a machine that has it (docs/WEB.md, section 3)"
echo "ok: docker, uv, make, openssl, demo data"

step "2/7 make setup"
make setup

if [ "$MODE" = "full" ]; then
  step "lint, type-check, tests (full)"
  make lint
  make type-check
  make test
  make test-web
fi

step "3/7 make docker-up (RECOGNIZER=$RECOGNIZER, PIPELINE_CONFIG=$PIPELINE_CONFIG)"
make docker-up

step "4/7 make seed-demo"
make seed-demo

step "5/7 test accounts"
# the password is the text after the last ": " of the command's last line; it stays in a variable
new_password() {
  (cd backend && uv run python -m entrypoints.reset_password "$1" --create --role "$2") | tail -n 1 | sed 's/^.*: //' | tr -d '\r'
}
SMOKE_ADMIN_PW="$(new_password e2e_admin admin)"
SMOKE_STAFF_PW="$(new_password e2e_staff staff)"
[ -n "$SMOKE_ADMIN_PW" ] && [ -n "$SMOKE_STAFF_PW" ] || fail "could not create the test accounts"
echo "ok: e2e_admin, e2e_staff (passwords not shown)"

step "6/7 make check-env"
make check-env

step "7/7 make smoke"
SMOKE_ADMIN=e2e_admin SMOKE_ADMIN_PW="$SMOKE_ADMIN_PW" SMOKE_STAFF=e2e_staff SMOKE_STAFF_PW="$SMOKE_STAFF_PW" make smoke

printf '\n==== E2E PASSED ====\n'
echo "web: http://localhost:5173    api: http://localhost:8000/api/health"
echo "Log in with your own account. To create one: make reset-password USER_NAME=admin ROLE=admin"
echo "Stop everything: make docker-down"
if [ -z "${NO_OPEN:-}" ]; then
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) cmd.exe //c start "" "http://localhost:5173" > /dev/null 2>&1 || true ;;
    Darwin) open "http://localhost:5173" > /dev/null 2>&1 || true ;;
    *) xdg-open "http://localhost:5173" > /dev/null 2>&1 || true ;;
  esac
fi
