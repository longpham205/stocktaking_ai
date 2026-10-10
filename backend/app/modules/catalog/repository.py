"""Postgres persistence for the catalog as the web sees it. The only place in the module that runs SQL.

The product tables are the engine's (backend/engine/catalog/db.py, built by migration 0002).

Reads (`CatalogRepository`) use plain SQL instead of `engine.catalog`: an API process started with
the fake recognizer must not import the engine at startup, and a query always sees what the admin
last saved, with no in-memory copy to reload.

Writes (`CatalogEdits`) go through the engine's own edit functions (`engine.catalog.edits`), on one
synchronous session that also writes the change log: catalog and log commit together. The engine is
imported there, at the first admin edit, not at startup.
"""

import copy
import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Boolean, ColumnElement, Engine, Integer, String, and_, column, delete, func, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import AppError, Conflict, NotFound
from app.modules.audit.ports import Change
from app.modules.audit.repository import record_changes_sync
from app.modules.catalog.models import ProductPriceRow
from app.modules.catalog.ports import ColorRef, Product
from app.modules.inventory.models import ProductStockRow
from app.modules.inventory.repository import set_stock_sync

_PRICE = ProductPriceRow
_STOCK = ProductStockRow
_P = table(
    "product",
    column("product_id", String),
    column("product_name", String),
    column("barcode", String),
    column("gallery_folder", String),
    column("is_active", Boolean),
    column("needs_naming", Boolean),
)
_E = table(
    "product_evidence",
    column("product_id", String),
    column("evidence_type", String),
    column("value_json", String),
)
_C = table(
    "color_reference",
    column("color_code", String),
    column("r", Integer),
    column("g", Integer),
    column("b", Integer),
    column("hex", String),
    column("source", String),
)


def _parse(product_id: str, evidence_type: str, value_json: str) -> Any:
    try:
        return json.loads(value_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"value_json hỏng ở bằng chứng {evidence_type} của SKU {product_id}: {exc}") from exc


def _color_code(product_id: str, value_json: str | None) -> str | None:
    if value_json is None:
        return None
    value = _parse(product_id, "color_code", value_json)
    return str(value) if value else None


def _evidence_json(value: Any) -> str | None:
    """Evidence as the change log writes it."""
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _id_order(pid: str) -> tuple[int, str]:
    return (len(pid), pid)


class CatalogRepository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def active_products(self) -> list[Product]:
        """Every product on sale with its current price, in no particular order."""
        return await self._products(_P.c.is_active)

    async def products_by_id(self, product_ids: Iterable[str]) -> dict[str, Product]:
        """The products with these ids, on sale or not: an old order still shows its names."""
        ids = list(product_ids)
        if not ids:
            return {}
        return {product.id: product for product in await self._products(_P.c.product_id.in_(ids))}

    async def _products(self, condition: ColumnElement[bool]) -> list[Product]:
        query = (
            select(
                _P.c.product_id,
                _P.c.product_name,
                _P.c.barcode,
                _P.c.needs_naming,
                _PRICE.price,
                _STOCK.quantity,
                _E.c.value_json,
            )
            .select_from(_P)
            .outerjoin(_PRICE, _PRICE.product_id == _P.c.product_id)
            .outerjoin(_STOCK, _STOCK.product_id == _P.c.product_id)
            .outerjoin(_E, and_(_E.c.product_id == _P.c.product_id, _E.c.evidence_type == "color_code"))
            .where(condition)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
            colors = set((await conn.execute(select(_C.c.color_code))).scalars())
        products = []
        for row in rows:
            code = _color_code(row["product_id"], row["value_json"])
            products.append(
                Product(
                    id=row["product_id"],
                    name=row["product_name"],
                    barcode=row["barcode"] or "",
                    price=row["price"],
                    stock=row["quantity"],
                    needs_naming=row["needs_naming"],
                    missing_color_reference=code is not None and code not in colors,
                )
            )
        return products

    async def evidence(self, product_id: str | None = None) -> dict[str, dict[str, Any]]:
        """Parsed evidence {product_id: {type: value}}, of one product or of all."""
        query = select(_E.c.product_id, _E.c.evidence_type, _E.c.value_json)
        if product_id is not None:
            query = query.where(_E.c.product_id == product_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        out: dict[str, dict[str, Any]] = {}
        for pid, evidence_type, value_json in rows:
            out.setdefault(pid, {})[evidence_type] = _parse(pid, evidence_type, value_json)
        return out

    async def colors(self) -> list[ColorRef]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(_C).order_by(_C.c.color_code))).mappings().all()
        return [ColorRef(row["color_code"], row["hex"], row["r"], row["g"], row["b"], row["source"]) for row in rows]

    async def gallery_folder(self, product_id: str) -> str | None:
        async with self.engine.connect() as conn:
            query = select(_P.c.gallery_folder).where(_P.c.product_id == product_id)
            return (await conn.execute(query)).scalar_one_or_none()


class CatalogEdits:
    """The admin's catalog writes, synchronous (the engine's session): call them in a worker thread."""

    def __init__(self, sync_database_url: str):
        self.url = sync_database_url
        self._engine: Engine | None = None

    def _session(self) -> Any:
        from sqlmodel import Session

        if self._engine is None:
            from engine.catalog.db import make_engine_from_url

            self._engine = make_engine_from_url(self.url)
        return Session(self._engine)

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()

    def update_product(self, product_id: str, fields: dict[str, Any], changed_by: int) -> bool:
        """Apply `price`, `stock`, `barcode` (None = remove), `name` with their log entries, in one
        transaction. Returns whether the engine's catalog changed (price and stock are the web's)."""
        from engine.catalog.edits import BarcodeTaken, ProductNotFound, set_barcode, set_name

        changes: list[Change] = []
        catalog_changed = False
        with self._session() as session:
            conn = session.connection()
            if "price" in fields:
                price = fields["price"]
                current = select(_PRICE.price).where(_PRICE.product_id == product_id).with_for_update()
                old = conn.execute(current).scalar_one_or_none()
                if old != price:
                    changes.append(
                        Change("price", None if old is None else str(old), None if price is None else str(price))
                    )
                    if price is None:
                        conn.execute(delete(_PRICE).where(_PRICE.product_id == product_id))
                    else:
                        statement = insert(_PRICE).values(product_id=product_id, price=price)
                        conn.execute(
                            statement.on_conflict_do_update(
                                index_elements=[_PRICE.product_id], set_={"price": price, "updated_at": func.now()}
                            )
                        )
            if "stock" in fields:
                stock = fields["stock"]
                old_stock = set_stock_sync(conn, product_id, stock)
                if old_stock != stock:
                    before = None if old_stock is None else str(old_stock)
                    changes.append(Change("stock", before, None if stock is None else str(stock)))
            try:
                if "barcode" in fields:
                    old_barcode, new_barcode = set_barcode(session, product_id, fields["barcode"])
                    if old_barcode != new_barcode:
                        changes.append(Change("barcode", old_barcode or "", new_barcode or ""))
                        catalog_changed = True
                if "name" in fields:
                    old_name, renamed = set_name(session, product_id, fields["name"])
                    if renamed:
                        changes.append(Change("name", old_name, fields["name"]))
                        catalog_changed = True
            except ProductNotFound as exc:
                raise NotFound("Không thấy sản phẩm") from exc
            except BarcodeTaken as exc:
                raise Conflict("Barcode đã thuộc sản phẩm khác", code="BARCODE_DUPLICATE") from exc
            record_changes_sync(conn, "product", product_id, changes, changed_by)
            session.commit()
        return catalog_changed

    def update_evidence(
        self, product_id: str, fields: dict[str, Any], changed_by: int, username: str, ocr_min_length: int
    ) -> tuple[list[str], bool]:
        """Apply normalised evidence fields (None = remove) to one product. `confusable_with` is a
        pair: it is written on both products. The whole catalog's evidence is validated before
        anything is saved. Returns the validation warnings and whether anything changed."""
        from engine.catalog.edits import all_evidence, color_codes, product_ids, put_evidence
        from engine.catalog.validation import validate_catalog

        with self._session() as session:
            old = all_evidence(session)
            new = copy.deepcopy(old)  # `old` stays as it was, to diff against
            mine = new.setdefault(product_id, {})
            for field, value in fields.items():
                if field == "confusable_with":
                    before, after = set(mine.get(field) or []), set(value or [])
                    for other in after - before:
                        pairs = new.setdefault(other, {}).setdefault(field, [])
                        if product_id not in pairs:
                            pairs.append(product_id)
                            pairs.sort(key=_id_order)
                    for other in before - after:
                        pairs = [x for x in new.get(other, {}).get(field, []) if x != product_id]
                        if pairs:
                            new[other][field] = pairs
                        else:
                            new.get(other, {}).pop(field, None)
                if value is None:
                    mine.pop(field, None)
                else:
                    mine[field] = value
            report = validate_catalog(
                product_ids(session), {k: v for k, v in new.items() if v}, color_codes(session), ocr_min_length
            )
            if report.errors:
                raise AppError(
                    "Bằng chứng không hợp lệ: " + "; ".join(report.errors),
                    code="EVIDENCE_INVALID",
                    status_code=422,
                    errors=list(report.errors),
                )
            conn = session.connection()
            changed = False
            for pid in sorted(set(old) | set(new), key=_id_order):
                for field in sorted(set(old.get(pid, {})) | set(new.get(pid, {}))):
                    before_value, after_value = old.get(pid, {}).get(field), new.get(pid, {}).get(field)
                    if before_value == after_value:
                        continue
                    put_evidence(session, pid, field, after_value, username)
                    change = Change(field, _evidence_json(before_value), _evidence_json(after_value))
                    record_changes_sync(conn, "product_evidence", pid, [change], changed_by)
                    changed = True
            session.commit()
        return list(report.warnings), changed

    def update_color(self, color_code: str, hex_value: str | None, changed_by: int) -> bool:
        """Create, change or (hex None) remove a colour reference, with its log entry."""
        from engine.catalog.edits import set_color

        with self._session() as session:
            old, new = set_color(session, color_code, hex_value)
            if old == new:
                return False
            record_changes_sync(
                session.connection(), "color_reference", color_code, [Change("hex", old, new)], changed_by
            )
            session.commit()
        return True
