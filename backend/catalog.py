"""Catalog sản phẩm cho web: đọc bảng catalog trong app.db qua ``CatalogRepository`` của ``src.catalog``.

Web và pipeline dùng CHUNG một file app.db (catalog + bảng web). Ghi catalog (barcode, tên, bằng chứng,
màu tham chiếu) do ``backend/service.py`` làm bằng SQL trong cùng giao dịch với ``change_log``;
sau khi ghi gọi ``reload()``.
"""

from __future__ import annotations

from pathlib import Path

from src.catalog.repository import SqliteCatalogRepository


class DbCatalog:
    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self.repo = SqliteCatalogRepository(self.path)
        self._view: dict[str, dict] = {}
        self._build_view()

    def _build_view(self) -> None:
        view = {
            pid: {"id": pid, "name": p.product_name, "barcode": p.barcode or "",
                  "needs_naming": p.needs_naming, "is_active": p.is_active}
            for pid, p in self.repo.products().items()
        }
        if not any(v["is_active"] for v in view.values()):
            raise ValueError(f"Catalog rỗng: {self.path} (chạy python -m src.catalog.migrate ...).")
        self._view = view

    def reload(self) -> None:
        self.repo.reload()
        self._build_view()

    def version(self) -> str:
        return self.repo.version()

    def all(self) -> dict[str, dict]:
        """SKU đang bán (dùng cho tìm kiếm, thêm món, quản trị)."""
        return {pid: v for pid, v in self._view.items() if v["is_active"]}

    def get(self, product_id: str) -> dict | None:
        """Mọi SKU kể cả ngừng bán (đơn cũ vẫn hiển thị được tên)."""
        return self._view.get(str(product_id))
