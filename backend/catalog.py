"""Nguồn catalog CHỈ ĐỌC cho web (MVP: đọc products.json qua interface mỏng).

Đây là điểm sẽ thay bằng `CatalogRepository` (SQLite) ở Phase 1B mà không sửa nơi gọi. Web không bao giờ
sửa `products.json`; giá và barcode do admin chỉnh nằm trong DB của web (`product_prices`, `product_overrides`).
`product_id` là chuẩn và bất biến (= category_id của nhãn benchmark) — không đổi số, không lấp khoảng trống.
"""

from __future__ import annotations

import json
from pathlib import Path


class JsonCatalog:
    def __init__(self, products_path: Path) -> None:
        self.path = Path(products_path)
        self._products: dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"Không thấy catalog: {self.path} (chạy build pipeline trước).")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("products", [])
        products: dict[str, dict] = {}
        for it in items:
            pid = str(it.get("product_id", "")).strip()
            if not pid:
                continue
            products[pid] = {
                "id": pid,
                "name": str(it.get("product_name") or f"SKU {pid}"),
                "barcode": str(it.get("barcode") or ""),
            }
        if not products:
            raise ValueError(f"Catalog rỗng: {self.path}")
        self._products = dict(sorted(products.items(), key=lambda kv: (len(kv[0]), kv[0])))

    def all(self) -> dict[str, dict]:
        return self._products

    def get(self, product_id: str) -> dict | None:
        return self._products.get(str(product_id))
