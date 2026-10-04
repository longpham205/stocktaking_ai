"""Nguồn catalog dạng file JSON (``catalog_snapshot.json``) cho môi trường không có DB (Colab/Kaggle).

Snapshot là bản XUẤT CÓ CHỦ ĐÍCH từ DB (``scripts/export_catalog_snapshot.py``), không phải
fallback: pipeline chọn nguồn bằng ``catalog.source``. Module này không import ``engine.catalog.db``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from engine.catalog.repository import BaseCatalogRepository, CatalogData


class JsonSnapshotCatalogRepository(BaseCatalogRepository):
    def __init__(self, snapshot_path: str | Path) -> None:
        self._path = Path(snapshot_path)
        if not self._path.is_file():
            raise FileNotFoundError(
                f"Không thấy snapshot catalog: {self._path}. Xuất bằng: python scripts/export_catalog_snapshot.py"
            )
        super().__init__()

    def _load(self) -> CatalogData:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Snapshot catalog không phải JSON hợp lệ: {self._path}: {exc}") from exc
        return CatalogData.from_snapshot_dict(data)


def write_snapshot(data: CatalogData, out_path: str | Path) -> Path:
    """Ghi snapshot nguyên tử (file tạm rồi đổi tên) để không bao giờ để lại file dở."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(data.to_snapshot_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    return out


__all__ = ["JsonSnapshotCatalogRepository", "write_snapshot"]
