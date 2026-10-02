"""Cấu hình backend: `configs/backend.yaml` + `.env` (bí mật) + tham số dòng lệnh."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS: dict[str, Any] = {
    "server": {"host": "0.0.0.0", "port": 8000, "max_upload_mb": 10, "allowed_origins": []},
    "auth": {"token_ttl_minutes": 480, "max_failed_attempts": 5, "lockout_minutes": 5},
    "inference": {"queue_max": 20, "timeout_seconds": 60, "idempotency_window_seconds": 5},
    "db": {"path": "data/db/app.db"},
    "media": {"root": "data/transactions", "url_ttl_seconds": 600, "retention_days": 30, "thumb_width": 240},
    "pos": {"allow_checkout_without_price": False, "timezone_offset_hours": 7},
}

_ENV_KEYS = ("JWT_SECRET", "SEED_STAFF_PASSWORD", "SEED_ADMIN_PASSWORD")


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def ensure_env(env_path: Path, announce: bool = True) -> dict[str, str]:
    """Đảm bảo `.env` có bí mật ngẫu nhiên. KHÔNG bao giờ ghi đè giá trị đã có.

    Thiếu khoá nào thì sinh ngẫu nhiên và in mật khẩu MỘT LẦN ra console (không có bí mật mặc định).
    """
    values: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    created: dict[str, str] = {}
    for key in _ENV_KEYS:
        if not values.get(key):
            created[key] = secrets.token_urlsafe(32 if key == "JWT_SECRET" else 9)
    if created:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        with env_path.open("a", encoding="utf-8", newline="\n") as fh:
            if env_path.exists() and env_path.stat().st_size and not env_path.read_text(encoding="utf-8").endswith("\n"):
                fh.write("\n")
            for k, v in created.items():
                fh.write(f"{k}={v}\n")
        values.update(created)
        if announce:
            print("=" * 64)
            print(f"Đã tạo bí mật mới trong {env_path.name} (chỉ hiện MỘT LẦN):")
            for k in ("SEED_STAFF_PASSWORD", "SEED_ADMIN_PASSWORD"):
                if k in created:
                    print(f"  {'staff' if 'STAFF' in k else 'admin'} / {created[k]}")
            print("=" * 64)
    for k, v in values.items():
        os.environ.setdefault(k, v)
    return values


@dataclass
class Settings:
    host: str
    port: int
    max_upload_bytes: int
    allowed_origins: list[str]
    token_ttl_seconds: int
    max_failed_attempts: int
    lockout_seconds: int
    queue_max: int
    job_timeout_seconds: float
    idempotency_window_seconds: float
    db_path: Path
    media_root: Path
    media_url_ttl: int
    media_retention_days: int
    thumb_width: int
    allow_checkout_without_price: bool
    tz_offset_hours: float
    pipeline_config: Path
    metadata_dir: Path
    products_filename: str
    frontend_dir: Path
    seed_prices_path: Path | None
    jwt_secret: str = field(repr=False, default="")
    seed_staff_password: str = field(repr=False, default="")
    seed_admin_password: str = field(repr=False, default="")


def load_settings(
    pipeline_config: str | Path,
    backend_config: str | Path | None = None,
    data_dir: str | Path | None = None,
    host: str | None = None,
    port: int | None = None,
) -> Settings:
    import yaml  # PyYAML đã có trong requirements.txt

    cfg = dict(DEFAULTS)
    bpath = Path(backend_config) if backend_config else ROOT / "configs" / "backend.yaml"
    if not bpath.is_absolute():
        bpath = ROOT / bpath
    if bpath.is_file():
        cfg = _deep_merge(cfg, yaml.safe_load(bpath.read_text(encoding="utf-8")) or {})

    pcfg_path = Path(pipeline_config)
    if not pcfg_path.is_absolute():
        pcfg_path = ROOT / pcfg_path
    if not pcfg_path.is_file():
        raise FileNotFoundError(f"Không thấy config pipeline: {pcfg_path}")
    pipe = yaml.safe_load(pcfg_path.read_text(encoding="utf-8")) or {}
    meta_dir = ROOT / pipe.get("paths", {}).get("metadata_dir", "data/metadata")
    products_filename = pipe.get("catalog", {}).get("products_filename", "products.json")

    def under(p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else ROOT / p

    if data_dir:
        base = under(data_dir)
        db_path, media_root = base / "db" / "app.db", base / "transactions"
        seed_prices = base / "seed" / "product_prices.json"
    else:
        db_path, media_root = under(cfg["db"]["path"]), under(cfg["media"]["root"])
        seed_prices = ROOT / "data" / "seed" / "product_prices.json"

    env = ensure_env(ROOT / ".env")
    s = cfg["server"]
    return Settings(
        host=host or s["host"], port=int(port or s["port"]),
        max_upload_bytes=int(s["max_upload_mb"]) * 1024 * 1024,
        allowed_origins=list(s.get("allowed_origins") or []),
        token_ttl_seconds=int(cfg["auth"]["token_ttl_minutes"]) * 60,
        max_failed_attempts=int(cfg["auth"]["max_failed_attempts"]),
        lockout_seconds=int(cfg["auth"]["lockout_minutes"]) * 60,
        queue_max=int(cfg["inference"]["queue_max"]),
        job_timeout_seconds=float(cfg["inference"]["timeout_seconds"]),
        idempotency_window_seconds=float(cfg["inference"]["idempotency_window_seconds"]),
        db_path=db_path, media_root=media_root,
        media_url_ttl=int(cfg["media"]["url_ttl_seconds"]),
        media_retention_days=int(cfg["media"]["retention_days"]),
        thumb_width=int(cfg["media"]["thumb_width"]),
        allow_checkout_without_price=bool(cfg["pos"]["allow_checkout_without_price"]),
        tz_offset_hours=float(cfg["pos"]["timezone_offset_hours"]),
        pipeline_config=pcfg_path, metadata_dir=meta_dir, products_filename=products_filename,
        frontend_dir=ROOT / "frontend",
        seed_prices_path=seed_prices if seed_prices.is_file() else None,
        jwt_secret=env["JWT_SECRET"], seed_staff_password=env["SEED_STAFF_PASSWORD"],
        seed_admin_password=env["SEED_ADMIN_PASSWORD"],
    )
