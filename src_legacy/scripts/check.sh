#!/usr/bin/env bash
# Kiểm tra nhanh toàn dự án: cú pháp Python -> cú pháp JS -> pytest.
# Chỉ in dòng lỗi + tóm tắt; dừng ở bước lỗi đầu tiên. Chạy từ bất kỳ đâu: bash scripts/check.sh
# (Dự án chưa có ruff/mypy; "lint" = compileall + node --check.)
set -u
cd "$(dirname "$0")/.."

if [ -x venv/Scripts/python.exe ]; then PY=venv/Scripts/python.exe
elif [ -x venv/bin/python ]; then PY=venv/bin/python
else PY=python; fi
export PYTHONIOENCODING=utf-8

step() { printf '%-28s' "[$1]"; }

step "syntax python"
if ! out=$("$PY" -m compileall -q src backend scripts tests debug run.py 2>&1); then
  echo "LỖI"; echo "$out" | grep -E "Error|File" | head -20; exit 1
fi
echo "OK"

step "syntax js (frontend)"
if command -v node >/dev/null 2>&1; then
  if ! out=$(node --check frontend/app.js 2>&1); then echo "LỖI"; echo "$out" | head -10; exit 1; fi
  echo "OK"
else
  echo "BỎ QUA (không có node)"
fi

step "pytest"
out=$("$PY" -m pytest -q -p no:cacheprovider "$@" 2>&1); rc=$?
if [ $rc -ne 0 ]; then
  echo "LỖI"
  echo "$out" | grep -E "^(FAILED|ERROR)|Error:|error:" | head -30
  echo "$out" | tail -1
  exit $rc
fi
echo "OK — $(echo "$out" | tail -1)"
