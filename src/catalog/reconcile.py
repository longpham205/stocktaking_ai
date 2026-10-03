"""So khớp thư mục gallery với catalog (hàm thuần, dùng chung pipeline + backend).

Không đụng DB/đĩa: nhận dữ liệu, trả ``ReconcilePlan`` để ``sync_gallery`` áp dụng.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def id_folder_name(product_id: str | int) -> str:
    """Tên thư mục chuẩn cho SKU mới: ID đệm 4 chữ số (ASCII, ổn định khi đổi tên sản phẩm)."""
    return f"{int(product_id):04d}"


@dataclass
class ReconcilePlan:
    new_folders: list[str] = field(default_factory=list)          # thư mục trong gallery chưa gắn SKU
    missing_folders: dict[str, str] = field(default_factory=dict)  # product_id -> thư mục không còn trên đĩa
    image_count_updates: dict[str, int] = field(default_factory=dict)  # product_id -> số ảnh mới

    @property
    def changed(self) -> bool:
        return bool(self.new_folders or self.image_count_updates)


def reconcile(
    catalog_folders: dict[str, str],
    catalog_image_counts: dict[str, int],
    gallery_folders: dict[str, int],
) -> ReconcilePlan:
    """``catalog_folders``: thư mục -> product_id (mọi SKU có thư mục, kể cả ngừng bán);
    ``catalog_image_counts``: product_id -> image_count trong DB; ``gallery_folders``: thư mục -> số ảnh.
    """
    plan = ReconcilePlan()
    for folder in sorted(gallery_folders):
        pid = catalog_folders.get(folder)
        if pid is None:
            plan.new_folders.append(folder)
        elif catalog_image_counts.get(pid) != gallery_folders[folder]:
            plan.image_count_updates[pid] = gallery_folders[folder]
    for folder, pid in sorted(catalog_folders.items()):
        if folder not in gallery_folders:
            plan.missing_folders[pid] = folder
    return plan


__all__ = ["ReconcilePlan", "id_folder_name", "reconcile"]
