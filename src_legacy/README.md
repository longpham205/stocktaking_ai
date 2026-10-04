# src_legacy — web POS v1 (đóng băng)

Code web thế hệ đầu, giữ lại **chỉ để tham khảo** trong lúc viết lại theo kiến trúc mới
(`backend/` FastAPI + `frontend/` React — xem `plans/261003-2217-fullstack-refactor-backend-frontend/`).

**Không import, không sửa, không chạy tại chỗ**: các file ở đây vẫn import `src.*` và giả định layout cũ
(`configs/`, `data/` ở gốc repo), nên không còn chạy được từ vị trí này.

| Thư mục / file | Nội dung | Thay thế bởi |
|---|---|---|
| `backend/` | Server stdlib (`ThreadingHTTPServer`), ~35 endpoint `/api/*`, SQLite | `backend/app/` (FastAPI, Postgres) |
| `frontend/` | Vanilla JS (`app.js`, `app.css`, `index.html`) | `frontend/` (Vite + React) |
| `configs/backend.yaml` | Cấu hình web | `.env` + pydantic-settings |
| `scripts/` | `check_env`, `db_snapshot`, `reset_password`, `setup`, `check.sh`, `smoke_web.sh` | Make targets / entrypoints (phase 7) |
| `tests/` | Test API web, smoke test giao diện, test các script trên | `backend/tests/`, `frontend/src/**/*.test.tsx` |
| `engine/ui/` | Giao diện desktop tkinter của pipeline | Trang Admin trên web |
| `bin/`, `launch.bat`, `requirements.txt` | Cài đặt + khởi chạy bằng venv | `uv` + `Makefile` + `docker compose` |

## Muốn chạy lại bản cũ
Dùng commit trước refactor trong một worktree riêng (không đụng tới cây làm việc hiện tại):

```bash
git worktree add ../stocktaking-legacy f30710d
cd ../stocktaking-legacy && python -m backend --fake --config configs/config.demo.yaml --data-dir data_demo
```
