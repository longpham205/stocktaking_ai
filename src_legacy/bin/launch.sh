#!/usr/bin/env bash
# Khoi dong Web POS.  Cach dung:  ./bin/launch.sh [demo|real] [tham so them cho backend]
#   demo: configs/config.demo.yaml + data_demo (du lieu tong hop, backend mock)   real: configs/config.yaml + data (mac dinh)
set -u
cd "$(dirname "$0")/.."
CFG=configs/config.yaml; DATA=data; MODE=real
case "${1:-}" in
  demo) MODE=demo; CFG=configs/config.demo.yaml; DATA=data_demo; shift ;;
  real) shift ;;
esac
if [ -f venv/bin/activate ]; then . venv/bin/activate
elif [ -f .venv/bin/activate ]; then . .venv/bin/activate
else echo "[CANH BAO] Khong thay venv hoac .venv, dung Python hien tai."; fi
echo; echo "==== Kiem tra moi truong: che do $MODE ===="
if ! python scripts/check_env.py --config "$CFG" --data-dir "$DATA" "$@"; then
  echo; echo "Moi truong chua san sang. Hay sua cac dong [FAIL] o tren roi chay lai."; exit 1
fi
echo; echo "==== Khoi dong Web POS ===="
exec python -m backend --config "$CFG" --data-dir "$DATA" "$@"
