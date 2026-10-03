#!/usr/bin/env bash
# Smoke test giao diện web trên DB TẠM (migrate từ data_demo/seed), server --fake, không đụng data_demo/db.
# Dùng: bash scripts/smoke_web.sh   (cần .env đã có SEED_STAFF_PASSWORD / SEED_ADMIN_PASSWORD; không in mật khẩu)
set -u
cd "$(dirname "$0")/.."
if [ -x venv/Scripts/python.exe ]; then PY=venv/Scripts/python.exe; elif [ -x venv/bin/python ]; then PY=venv/bin/python; else PY=python; fi
export PYTHONIOENCODING=utf-8
PORT="${PORT:-8765}"
W="$(mktemp -d)"; CFG="configs/_tmp_smoke.yaml"
cleanup() { [ -n "${SRV:-}" ] && kill "$SRV" 2>/dev/null; rm -f "$CFG"; rm -rf "$W"; }
trap cleanup EXIT
mkdir -p "$W/db"
"$PY" -m src.catalog.migrate --seed-dir data_demo/seed --legacy-config configs/config.demo.yaml --db "$W/db/app.db" > "$W/migrate.log" 2>&1 \
  || { echo "LỖI migrate:"; tail -5 "$W/migrate.log"; exit 1; }
"$PY" - "$W/db/app.db" "$CFG" <<'EOF'
import sys, yaml
c = yaml.safe_load(open("configs/config.demo.yaml", encoding="utf-8"))
c["catalog"]["db_path"] = sys.argv[1]
yaml.safe_dump(c, open(sys.argv[2], "w", encoding="utf-8"), allow_unicode=True)
EOF
SP=$(grep '^SEED_STAFF_PASSWORD=' .env | cut -d= -f2-); AP=$(grep '^SEED_ADMIN_PASSWORD=' .env | cut -d= -f2-)
"$PY" -m backend --config "$CFG" --data-dir "$W" --fake --port "$PORT" > "$W/server.log" 2>&1 &
SRV=$!
for _ in $(seq 1 60); do curl -s "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.5; done
XP=$(grep '^SEED_ADVANCED_PASSWORD=' .env | cut -d= -f2-)  # server tự sinh khi .env chưa có
node tests/frontend_smoke.js "http://127.0.0.1:$PORT" "$SP" "$AP" data_demo/query/query_01.jpg "$XP" > "$W/smoke.log" 2>&1; rc=$?
echo "smoke: $(grep -c '^OK' "$W/smoke.log") OK, $(grep -c '^FAIL' "$W/smoke.log") FAIL (rc=$rc)"
grep '^FAIL\|LỖI' "$W/smoke.log" | head -10
exit $rc
