"""What this installation has and lacks, before a demo (`make check-env`).

    python -m entrypoints.check_env

One line per check: `OK`, `WARN` (works, but something to know) or `FAIL`. Exit code 1 when a check
failed. Secrets are only reported as set or missing, never printed.
"""

import importlib.util
import sys
from collections.abc import Callable

import yaml  # type: ignore[import-untyped]  # no stubs installed; only safe_load is used
from sqlalchemy import create_engine, text

from app.core.config import BACKEND_DIR, CoreSettings, get_settings
from app.core.db import sync_database_url
from app.modules.auth.config import get_auth_settings

Result = tuple[str, str]  # (OK | WARN | FAIL, detail)


def check_secrets(settings: CoreSettings) -> Result:
    missing = [
        name
        for name, value in (
            ("JWT_SECRET", get_auth_settings().jwt_secret),
            ("MEDIA_URL_SECRET", settings.media_url_secret),
        )
        if not value
    ]
    return ("FAIL", f"thiếu {', '.join(missing)} (chạy make setup)") if missing else ("OK", "đã đặt")


def check_database(settings: CoreSettings) -> Result:
    engine = create_engine(sync_database_url(settings.database_url))
    try:
        with engine.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            products = conn.execute(text("SELECT count(*) FROM product WHERE is_active")).scalar_one()
            priced = conn.execute(text("SELECT count(*) FROM product_prices")).scalar_one()
            users = conn.execute(text("SELECT count(*) FROM users WHERE is_active")).scalar_one()
            admins = conn.execute(text("SELECT count(*) FROM users WHERE is_active AND role = 'admin'")).scalar_one()
    except Exception as exc:
        return "FAIL", f"không kết nối hoặc chưa migrate ({type(exc).__name__}): make docker-up-data && make migrate"
    finally:
        engine.dispose()
    versions = BACKEND_DIR / "migrations" / "versions"
    head = max(path.name[:4] for path in versions.glob("[0-9]*.py"))
    detail = f"migration {revision} (mới nhất {head}); {products} SKU đang bán, {priced} có giá; {users} tài khoản ({admins} admin)"
    if revision != head:
        return "FAIL", detail + " — chạy make migrate"
    if products == 0:
        return "WARN", detail + " — catalog rỗng: make seed-demo hoặc make import-legacy"
    if admins == 0:
        return "WARN", detail + " — chưa có admin: make reset-password USER_NAME=admin ROLE=admin"
    return "OK", detail


def check_pipeline(settings: CoreSettings) -> Result:
    config = settings.pipeline_config
    if not config.is_file():
        return "FAIL", f"không thấy PIPELINE_CONFIG: {config}"
    loaded = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    gallery = (loaded.get("paths") or {}).get("gallery_dir")
    gallery_dir = None if not gallery else (BACKEND_DIR / gallery if not str(gallery).startswith(("/", "\\")) else None)
    detail = f"{config.name}, recognizer={settings.recognizer}"
    if gallery_dir is not None and not gallery_dir.is_dir():
        return "WARN", detail + f" — không thấy thư mục gallery {gallery_dir}"
    return "OK", detail


def check_media(settings: CoreSettings) -> Result:
    media = settings.media_dir
    if not media.exists():
        return "WARN", f"{media} chưa có (sẽ được tạo ở lượt chụp đầu tiên)"
    return "OK", f"{media}: {sum(1 for p in media.iterdir() if p.is_dir())} thư mục đơn"


def check_gpu(settings: CoreSettings) -> Result:
    if settings.recognizer == "fake":
        return "OK", "không cần (RECOGNIZER=fake)"
    if importlib.util.find_spec("torch") is None:
        return "WARN", "chưa cài torch: chỉ chạy được backend mock (config.demo.yaml); cài bằng uv sync --extra ml"
    import torch  # type: ignore[import-not-found]  # only in the ml extra

    if not torch.cuda.is_available():
        return "WARN", "torch có, không thấy GPU CUDA: pipeline thật sẽ chạy rất chậm trên CPU"
    return "OK", f"{torch.cuda.get_device_name(0)}"


CHECKS: list[tuple[str, Callable[[CoreSettings], Result]]] = [
    ("Bí mật (.env)", check_secrets),
    ("Cơ sở dữ liệu", check_database),
    ("Pipeline", check_pipeline),
    ("Ảnh chụp (MEDIA_DIR)", check_media),
    ("GPU", check_gpu),
]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    settings = get_settings()
    failed = False
    for name, check in CHECKS:
        status, detail = check(settings)
        failed = failed or status == "FAIL"
        print(f"{status:<4} {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
