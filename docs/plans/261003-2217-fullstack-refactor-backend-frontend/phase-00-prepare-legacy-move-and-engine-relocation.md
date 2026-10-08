# Phase 00 — Chuẩn bị, `src_legacy/`, chuyển engine

**Priority:** P0 · **Status:** DONE (2026-10-03, chưa commit) · Branch: `refactor/fullstack-layout` · Context: [design.md](design.md) §2, §5

## Đã làm
- [x] Baseline trước khi di chuyển (Python 3.12, macOS arm64): **281 pass / 25 fail / 0 skip**; 25 fail đều do thiếu `easyocr`. Đã chạy `python run.py --mode validate` trên `data_demo` và lưu `report.json` làm mốc so sánh.
- [x] Sinh `data_demo/` (`scripts/make_demo_dataset.py`, `engine.catalog.migrate`, validate). Script ghi đè `config.demo.yaml` (`confidence_threshold` 0.70 → 0.50) → **đã hoàn tác**, giữ giá trị đã commit.
- [x] `git mv` sang `src_legacy/`: `backend/`, `frontend/`, `bin/`, `launch.bat`, `requirements.txt`, `configs/backend.yaml`, `scripts/{check_env,db_snapshot,reset_password,setup}.py`, `scripts/{check,smoke_web}.sh`, `tests/{test_backend_api,test_check_env,test_db_snapshot,test_reset_password}.py`, `tests/frontend_smoke.js`, `src/ui` → `src_legacy/engine/ui`. Thêm `src_legacy/README.md`.
- [x] `git mv src backend/engine`; `configs/ scripts/ tests/ debug/ notebooks/` → `backend/`; `mv data/ weights/ data_demo/` → `backend/`.
- [x] Đổi import trong 103 file (929 chỗ, gồm `.py`, `.ipynb`, `.md`, `.yaml`): `src.<pkg>` → `engine.<pkg>` và `src/<pkg>` → `engine/<pkg>`, chỉ khi theo sau là tên package engine, để không đụng biến cục bộ tên `src`.
- [x] `run.py` → `backend/engine/__main__.py` (`python -m engine --mode infer|validate`), bỏ mode `ui`; cập nhật mọi hướng dẫn `python run.py`.
- [x] `backend/pyproject.toml`: uv, `>=3.11,<3.13`, deps nhẹ mặc định, extra `ml` (torch, rfdetr, sam2, transformers, easyocr), group `dev` (pytest, ruff); sinh `uv.lock`.
- [x] `.gitignore`: rule data/weights chuyển sang `backend/…`; bỏ rule toàn cục `lib/` và `build/` (sẽ nuốt `frontend/src/shared/lib`); thêm `node_modules/`, `frontend/dist`, `*.db`, `.ruff_cache`.
- [x] README gốc có banner "đang refactor".

## Lệch so với plan ban đầu
- **Không cần biến `STOCKTAKING_ROOT`.** `data/`, `weights/`, `debug/`, `notebooks/`, `tests/` vào thẳng `backend/` (không để ở root, không gom vào `research/`). Vì vậy bố cục tương đối giữ nguyên, mọi `parents[1]` và `config.parent.parent` vẫn đúng, không phải sửa dòng path nào.
- Test engine giữ phẳng ở `backend/tests/` (chưa tách `tests/engine/`).

## Kiểm chứng
| Kiểm tra | Kết quả |
|---|---|
| Test ID trước/sau (`--collect-only`) | Thiếu đúng 80 test web đã chuyển sang legacy (56 + 12 + 8 + 4), không thêm hay mất test engine nào |
| Pytest `backend/` | 201 pass / 25 fail; **tập fail giống hệt baseline** (thiếu `easyocr`) |
| `python -m engine --mode validate` (demo) | 266/266 record giống baseline, chỉ khác crop ID ngẫu nhiên và độ trễ |
| `compileall engine scripts tests debug` | OK |
| `--help` của 8 script + 13 debug tool | OK; chạy thật `debug/04_retrieval_selfcheck.py` cho 300/300 |
| ruff | 60 lỗi, tất cả có sẵn từ trước (bản gốc 64), không có lỗi mới do refactor |

## Vấn đề môi trường phát hiện (macOS arm64)
- `pyzbar` không tìm thấy `libzbar` của Homebrew → cần `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`. Dùng `DYLD_LIBRARY_PATH` sẽ làm torch nạp nhầm libpython và crash. Makefile phase 1 sẽ tự set khi chạy trên Darwin.
- Có đủ `--extra ml`: easyocr 1.7.2 + torch 2.14 **segfault** khi `load_state_dict` trên Mac → 25 test OCR/pipeline vẫn fail. Hướng xử lý: chạy trong Docker Linux, hoặc ghim `torch<2.14`, hoặc `pytest.importorskip("easyocr")` (quyết định ở phase 1).

## Việc chuyển sang phase sau
- Dọn 60 lỗi ruff có sẵn (commit riêng) khi thêm `make lint`.
- `scripts/check_env.py` / `db_snapshot.py` / `reset_password.py` viết lại cho Postgres (phase 7).
- Docs (`docs/*.md`, README) vẫn mô tả layout cũ, cập nhật ở phase 7.
