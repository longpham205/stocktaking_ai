"""Schema của catalog sản phẩm (định nghĩa MỘT lần, dùng chung pipeline + backend).

Chạy trên SQLite (file, ``make_engine``) hoặc trên DB bất kỳ theo URL (``make_engine_from_url``,
ví dụ Postgres của web: bảng do Alembic tạo, cùng định nghĩa này).

Bốn bảng: ``product``, ``product_evidence``, ``color_reference``, ``catalog_meta``.
Module thuần, không chạm GPU: import được ở mọi nơi có ``sqlmodel``.
``JsonSnapshotCatalogRepository`` KHÔNG import module này (Colab không cần DB).

Quy ước:
- ``product_id`` là chuỗi số = COCO ``category_id`` của benchmark; bất biến, không tái dùng.
- Màu tham chiếu lưu RGB + hex sRGB; việc đổi sang OpenCV Lab nằm trong ``Reranker``.
- Thời gian lưu dạng chuỗi ISO-8601 UTC (giống bảng của web).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, String, UniqueConstraint, event, text
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select

SCHEMA_VERSION = "1"

# evidence_type là danh sách MỞ (thêm loại mới không cần ALTER TABLE); luật ở validation.py.
COLOR_SOURCES = ("seed", "manual", "gallery")

META_NEXT_PRODUCT_ID = "next_product_id"
META_SCHEMA_VERSION = "schema_version"
META_SEED_COMPLETED_AT = "seed_completed_at"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Product(SQLModel, table=True):
    __tablename__ = "product"
    __table_args__ = (
        Index(
            "ux_product_barcode",
            "barcode",
            unique=True,
            sqlite_where=text("barcode IS NOT NULL AND barcode <> ''"),
            postgresql_where=text("barcode IS NOT NULL AND barcode <> ''"),
        ),
        Index(
            "ux_product_gallery_folder",
            "gallery_folder",
            unique=True,
            sqlite_where=text("gallery_folder IS NOT NULL AND gallery_folder <> ''"),
            postgresql_where=text("gallery_folder IS NOT NULL AND gallery_folder <> ''"),
        ),
        CheckConstraint("image_count >= 0", name="ck_product_image_count"),
    )

    product_id: str = Field(primary_key=True)
    product_name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    barcode: Optional[str] = None
    description: Optional[str] = None
    image_count: int = 0
    gallery_folder: Optional[str] = None
    is_active: bool = True
    needs_naming: bool = False
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ProductEvidence(SQLModel, table=True):
    __tablename__ = "product_evidence"
    __table_args__ = (UniqueConstraint("product_id", "evidence_type", name="ux_evidence_product_type"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: str = Field(
        sa_column=Column(String, ForeignKey("product.product_id", ondelete="RESTRICT"), nullable=False, index=True)
    )
    evidence_type: str
    value_json: str
    updated_by: Optional[str] = None
    updated_at: str = Field(default_factory=now_iso)


class ColorReference(SQLModel, table=True):
    __tablename__ = "color_reference"
    __table_args__ = (
        CheckConstraint("r BETWEEN 0 AND 255", name="ck_color_r"),
        CheckConstraint("g BETWEEN 0 AND 255", name="ck_color_g"),
        CheckConstraint("b BETWEEN 0 AND 255", name="ck_color_b"),
        CheckConstraint("source IN ('seed', 'manual', 'gallery')", name="ck_color_source"),
    )

    color_code: str = Field(primary_key=True)
    r: int
    g: int
    b: int
    hex: str
    source: str = "manual"
    updated_at: str = Field(default_factory=now_iso)


class CatalogMeta(SQLModel, table=True):
    __tablename__ = "catalog_meta"

    key: str = Field(primary_key=True)
    value: str


CATALOG_TABLES = [
    Product.__table__,
    ProductEvidence.__table__,
    ColorReference.__table__,
    CatalogMeta.__table__,
]


def _set_sqlite_pragmas(dbapi_connection, _record) -> None:
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
    finally:
        cur.close()


def make_engine(db_path: str | Path) -> Engine:
    """Engine SQLite cho file ``db_path`` (WAL, foreign_keys, busy_timeout). Tạo thư mục cha nếu thiếu."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def make_engine_from_url(db_url: str) -> Engine:
    """Engine cho catalog theo URL SQLAlchemy (``postgresql+psycopg://...`` hoặc ``sqlite:///...``).

    SQLite vẫn được bật WAL/foreign_keys/busy_timeout như ``make_engine``; DB khác dùng mặc định của driver.
    """
    if db_url.startswith("sqlite"):
        engine = create_engine(db_url, connect_args={"check_same_thread": False, "timeout": 5})
        event.listen(engine, "connect", _set_sqlite_pragmas)
        return engine
    return create_engine(db_url, pool_pre_ping=True)


def create_all(engine: Engine) -> None:
    """Tạo bảng catalog nếu chưa có và ghi ``schema_version``. Idempotent.

    Chỉ tạo bảng catalog (không đụng bảng của web dù dùng chung file).
    """
    SQLModel.metadata.create_all(engine, tables=CATALOG_TABLES)
    with Session(engine) as session:
        current = session.get(CatalogMeta, META_SCHEMA_VERSION)
        if current is None:
            session.add(CatalogMeta(key=META_SCHEMA_VERSION, value=SCHEMA_VERSION))
            session.commit()
        elif current.value != SCHEMA_VERSION:
            raise RuntimeError(
                f"Schema catalog trong DB là phiên bản {current.value}, code cần {SCHEMA_VERSION}. "
                "Cần migrate schema trước khi chạy."
            )


def get_meta(session: Session, key: str) -> Optional[str]:
    row = session.get(CatalogMeta, key)
    return row.value if row is not None else None


def set_meta(session: Session, key: str, value: str) -> None:
    """Ghi một khoá meta (chưa commit — để nằm trong transaction của bên gọi)."""
    row = session.get(CatalogMeta, key)
    if row is None:
        session.add(CatalogMeta(key=key, value=value))
    else:
        row.value = value
        session.add(row)


__all__ = [
    "SCHEMA_VERSION",
    "COLOR_SOURCES",
    "META_NEXT_PRODUCT_ID",
    "META_SCHEMA_VERSION",
    "META_SEED_COMPLETED_AT",
    "Product",
    "ProductEvidence",
    "ColorReference",
    "CatalogMeta",
    "CATALOG_TABLES",
    "make_engine",
    "make_engine_from_url",
    "create_all",
    "get_meta",
    "set_meta",
    "now_iso",
    "select",
    "Session",
]
