"""Sửa catalog từ bên ngoài (màn quản trị của web): barcode, tên, bằng chứng nhận diện, màu tham chiếu.

Mọi hàm ghi trên ``Session`` của BÊN GỌI và KHÔNG commit (cùng kiểu ``set_meta``): bên gọi ghi
nhật ký thay đổi của mình trên cùng kết nối rồi commit một lần, nên catalog và nhật ký luôn khớp.
Kiểm tra định dạng (barcode, mã màu, từ khoá OCR) là việc của bên gọi; ở đây chỉ kiểm điều cần DB:
SKU tồn tại, barcode chưa thuộc SKU khác.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from engine.catalog.db import ColorReference, Product, ProductEvidence, now_iso
from engine.catalog.validation import parse_hex


class ProductNotFound(LookupError):
    pass


class BarcodeTaken(ValueError):
    """Barcode đã thuộc SKU khác (ràng buộc ``ux_product_barcode``)."""


def _product(session: Session, product_id: str) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise ProductNotFound(f"Không có SKU {product_id} trong catalog")
    return product


def set_barcode(session: Session, product_id: str, barcode: str | None) -> tuple[str | None, str | None]:
    """Đặt barcode (None hoặc rỗng = bỏ barcode). Trả (cũ, mới); bằng nhau nghĩa là không đổi."""
    product = _product(session, product_id)
    new = barcode or None
    old = product.barcode or None
    if new == old:
        return old, new
    if new is not None:
        owner = session.exec(select(Product.product_id).where(Product.barcode == new)).first()
        if owner is not None and owner != product_id:
            raise BarcodeTaken(f"Barcode {new} đã thuộc SKU {owner}")
    product.barcode = new
    product.updated_at = now_iso()
    session.add(product)
    return old, new


def set_name(session: Session, product_id: str, name: str) -> tuple[str, bool]:
    """Đặt tên và gỡ cờ ``needs_naming``. Trả (tên cũ, có đổi không): đặt lại đúng tên cũ của SKU
    đang chờ đặt tên vẫn tính là đổi (cờ được gỡ)."""
    product = _product(session, product_id)
    old = product.product_name
    if name == old and not product.needs_naming:
        return old, False
    product.product_name = name
    product.needs_naming = False
    product.updated_at = now_iso()
    session.add(product)
    return old, True


def all_evidence(session: Session) -> dict[str, dict[str, Any]]:
    """Toàn bộ bằng chứng đã parse: {product_id: {evidence_type: giá trị}}."""
    out: dict[str, dict[str, Any]] = {}
    for row in session.exec(select(ProductEvidence)).all():
        out.setdefault(row.product_id, {})[row.evidence_type] = json.loads(row.value_json)
    return out


def put_evidence(session: Session, product_id: str, evidence_type: str, value: Any, updated_by: str | None) -> None:
    """Ghi (hoặc với ``value`` None: xoá) một loại bằng chứng của một SKU."""
    _product(session, product_id)
    row = session.exec(
        select(ProductEvidence).where(
            ProductEvidence.product_id == product_id, ProductEvidence.evidence_type == evidence_type
        )
    ).first()
    if value is None:
        if row is not None:
            session.delete(row)
        return
    if row is None:
        row = ProductEvidence(product_id=product_id, evidence_type=evidence_type, value_json="")
    row.value_json = json.dumps(value, ensure_ascii=False)
    row.updated_by = updated_by
    row.updated_at = now_iso()
    session.add(row)


def product_ids(session: Session) -> list[str]:
    return list(session.exec(select(Product.product_id)).all())


def color_codes(session: Session) -> list[str]:
    return list(session.exec(select(ColorReference.color_code)).all())


def set_color(session: Session, color_code: str, hex_value: str | None) -> tuple[str | None, str | None]:
    """Tạo/sửa màu tham chiếu (nguồn ``manual``) hoặc xoá (``hex_value`` None). ``hex_value`` đã
    chuẩn hoá ``#RRGGBB``. Trả (hex cũ, hex mới)."""
    row = session.get(ColorReference, color_code)
    old = row.hex if row is not None else None
    if old == hex_value:
        return old, hex_value
    if hex_value is None:
        assert row is not None
        session.delete(row)
        return old, None
    r, g, b = parse_hex(hex_value)
    if row is None:
        row = ColorReference(color_code=color_code, r=r, g=g, b=b, hex=hex_value)
    row.r, row.g, row.b, row.hex = r, g, b, hex_value
    row.source = "manual"
    row.updated_at = now_iso()
    session.add(row)
    return old, hex_value
