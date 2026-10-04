"""CatalogRepository — lớp DUY NHẤT pipeline dùng để đọc catalog (chỉ đọc).

Hai implementation, chọn bằng ``catalog.source``:
- ``SqliteCatalogRepository`` (máy chính, đọc ``app.db``) — ở file này;
- ``JsonSnapshotCatalogRepository`` (Colab, đọc ``catalog_snapshot.json``) — ở ``snapshot.py``.

Cả hai nạp dữ liệu MỘT lần thành ``CatalogData`` bất biến; ``reload()`` dựng bản mới rồi
đổi tham chiếu (gán thuộc tính là nguyên tử trong CPython). Module pipeline nên gọi
repository lúc dùng thay vì sao chép vào ``__init__``; cache bên ngoài gắn với ``version()``.

Module này không import ``engine.catalog.db`` ở mức module để ``snapshot.py`` dùng lại được
mà không cần SQLModel.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

SNAPSHOT_FORMAT = 1


@dataclass(frozen=True)
class ProductRecord:
    product_id: str
    product_name: str
    brand: str | None = None
    category: str | None = None
    barcode: str | None = None
    description: str | None = None
    image_count: int = 0
    gallery_folder: str | None = None
    is_active: bool = True
    needs_naming: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ColorRef:
    r: int
    g: int
    b: int
    hex: str
    source: str


@dataclass(frozen=True)
class CatalogData:
    """Ảnh chụp bất biến của catalog. Dựng bằng ``CatalogData.build``."""

    products: Mapping[str, ProductRecord]
    evidence: Mapping[str, Mapping[str, Any]]
    colors: Mapping[str, ColorRef]
    version: str

    @staticmethod
    def build(
        products: list[ProductRecord],
        evidence: dict[str, dict[str, Any]],
        colors: dict[str, ColorRef],
    ) -> "CatalogData":
        prod_map = {p.product_id: p for p in sorted(products, key=lambda p: _id_sort_key(p.product_id))}
        if len(prod_map) != len(products):
            raise ValueError("Catalog có product_id trùng lặp.")
        unknown = sorted(set(evidence) - set(prod_map), key=_id_sort_key)
        if unknown:
            raise ValueError(f"Bằng chứng trỏ tới product_id không tồn tại: {unknown}")
        ev = {
            pid: MappingProxyType({t: _freeze(v) for t, v in sorted(types.items())})
            for pid, types in sorted(evidence.items())
            if types
        }
        col = dict(sorted(colors.items()))
        payload = _canonical_payload(prod_map, ev, col)
        version = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        return CatalogData(MappingProxyType(prod_map), MappingProxyType(ev), MappingProxyType(col), version)

    def to_snapshot_dict(self) -> dict[str, Any]:
        """Dạng JSON chuẩn — dùng cho ``catalog_snapshot.json`` và để tính ``version``."""
        return {"format": SNAPSHOT_FORMAT, "version": self.version, **_canonical_payload(self.products, self.evidence, self.colors)}

    @staticmethod
    def from_snapshot_dict(data: Mapping[str, Any]) -> "CatalogData":
        fmt = data.get("format")
        if fmt != SNAPSHOT_FORMAT:
            raise ValueError(f"Định dạng snapshot catalog không hỗ trợ: {fmt!r} (cần {SNAPSHOT_FORMAT}).")
        products = [ProductRecord(**p) for p in data["products"]]
        evidence = {str(pid): dict(types) for pid, types in data.get("evidence", {}).items()}
        colors = {code: ColorRef(**c) for code, c in data.get("color_references", {}).items()}
        built = CatalogData.build(products, evidence, colors)
        declared = data.get("version")
        if declared is not None and declared != built.version:
            raise ValueError(
                f"Snapshot catalog bị sửa tay hoặc hỏng: version ghi {declared}, nội dung tính ra {built.version}."
            )
        return built


def _id_sort_key(pid: str) -> tuple[int, str]:
    return (len(pid), pid)


def _freeze(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    return value


def _canonical_payload(products, evidence, colors) -> dict[str, Any]:
    return {
        "products": [p.to_dict() for p in products.values()],
        "evidence": {pid: {t: _thaw(types[t]) for t in sorted(types)} for pid, types in evidence.items()},
        "color_references": {code: asdict(c) for code, c in colors.items()},
    }


@runtime_checkable
class CatalogRepository(Protocol):
    def products(self) -> Mapping[str, ProductRecord]: ...
    def get_product(self, product_id: str) -> dict[str, Any] | None: ...
    def folder_to_product_id(self) -> dict[str, str]: ...
    def evidence(self, product_id: str, evidence_type: str) -> Any: ...
    def force_evidence(self, product_id: str) -> frozenset[str]: ...
    def confusable_pairs(self) -> list[frozenset[str]]: ...
    def ocr_keywords(self, product_id: str) -> tuple[str, ...]: ...
    def color_code(self, product_id: str) -> str | None: ...
    def color_references(self) -> dict[str, tuple[int, int, int]]: ...
    def version(self) -> str: ...
    def reload(self) -> None: ...


class BaseCatalogRepository:
    """Cài đặt chung các truy vấn trên ``CatalogData``; lớp con chỉ cần ``_load()``."""

    def __init__(self) -> None:
        self._data: CatalogData = self._load()

    def _load(self) -> CatalogData:  # pragma: no cover - lớp con cài đặt
        raise NotImplementedError

    @property
    def data(self) -> CatalogData:
        return self._data

    def reload(self) -> None:
        self._data = self._load()

    def version(self) -> str:
        return self._data.version

    def products(self) -> Mapping[str, ProductRecord]:
        return self._data.products

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        rec = self._data.products.get(str(product_id))
        return rec.to_dict() if rec is not None else None

    def folder_to_product_id(self) -> dict[str, str]:
        """Thư mục gallery → product_id, chỉ SKU đang hoạt động có thư mục."""
        return {
            p.gallery_folder: pid
            for pid, p in self._data.products.items()
            if p.is_active and p.gallery_folder
        }

    def evidence(self, product_id: str, evidence_type: str) -> Any:
        """Giá trị đã parse của một loại bằng chứng, hoặc ``None`` nếu SKU chưa khai báo."""
        return self._data.evidence.get(str(product_id), {}).get(evidence_type)

    def force_evidence(self, product_id: str) -> frozenset[str]:
        return frozenset(self.evidence(product_id, "force_evidence") or ())

    def confusable_pairs(self) -> list[frozenset[str]]:
        pairs: set[frozenset[str]] = set()
        for pid, types in self._data.evidence.items():
            for other in types.get("confusable_with") or ():
                if str(other) != pid:
                    pairs.add(frozenset((pid, str(other))))
        return sorted(pairs, key=lambda pr: sorted(pr, key=_id_sort_key))

    def ocr_keywords(self, product_id: str) -> tuple[str, ...]:
        return tuple(self.evidence(product_id, "ocr_keywords") or ())

    def color_code(self, product_id: str) -> str | None:
        value = self.evidence(product_id, "color_code")
        return str(value) if value else None

    def color_references(self) -> dict[str, tuple[int, int, int]]:
        return {code: (c.r, c.g, c.b) for code, c in self._data.colors.items()}


class InMemoryCatalogRepository(BaseCatalogRepository):
    """Catalog dựng sẵn trong bộ nhớ (test, công cụ). ``reload()`` giữ nguyên dữ liệu."""

    def __init__(self, data: CatalogData) -> None:
        self._fixed = data
        super().__init__()

    def _load(self) -> CatalogData:
        return self._fixed


class SqliteCatalogRepository(BaseCatalogRepository):
    """Đọc catalog từ file SQLite (bảng tạo bởi ``engine.catalog.db``)."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        if not self._db_path.is_file():
            raise FileNotFoundError(
                f"Không thấy DB catalog: {self._db_path}. Chạy migrate trước: python -m engine.catalog.migrate ..."
            )
        super().__init__()

    def _load(self) -> CatalogData:
        from engine.catalog.db import make_engine

        return _read_catalog(make_engine(self._db_path))


class DatabaseCatalogRepository(BaseCatalogRepository):
    """Đọc catalog từ DB theo URL SQLAlchemy (Postgres của web). Bảng do Alembic tạo, cùng schema."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        super().__init__()

    def _load(self) -> CatalogData:
        from engine.catalog.db import make_engine_from_url

        return _read_catalog(make_engine_from_url(self._db_url))


def _read_catalog(engine: Any) -> CatalogData:
    """Nạp toàn bộ catalog qua một engine SQLAlchemy rồi đóng engine (dùng chung cho mọi nguồn DB)."""
    from engine.catalog.db import ColorReference, Product, ProductEvidence, Session, select

    try:
        with Session(engine) as s:
            products = [
                ProductRecord(
                    product_id=row.product_id,
                    product_name=row.product_name,
                    brand=row.brand,
                    category=row.category,
                    barcode=row.barcode,
                    description=row.description,
                    image_count=int(row.image_count),
                    gallery_folder=row.gallery_folder,
                    is_active=bool(row.is_active),
                    needs_naming=bool(row.needs_naming),
                )
                for row in s.exec(select(Product)).all()
            ]
            evidence: dict[str, dict[str, Any]] = {}
            for row in s.exec(select(ProductEvidence)).all():
                try:
                    value = json.loads(row.value_json)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"value_json hỏng ở bằng chứng {row.evidence_type} của SKU {row.product_id}: {exc}"
                    ) from exc
                evidence.setdefault(row.product_id, {})[row.evidence_type] = value
            colors = {
                row.color_code: ColorRef(r=row.r, g=row.g, b=row.b, hex=row.hex, source=row.source)
                for row in s.exec(select(ColorReference)).all()
            }
    finally:
        engine.dispose()
    return CatalogData.build(products, evidence, colors)


__all__ = [
    "SNAPSHOT_FORMAT",
    "ProductRecord",
    "ColorRef",
    "CatalogData",
    "CatalogRepository",
    "BaseCatalogRepository",
    "InMemoryCatalogRepository",
    "SqliteCatalogRepository",
    "DatabaseCatalogRepository",
]
