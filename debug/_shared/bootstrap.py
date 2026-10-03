"""Bootstrap dùng chung cho mọi script trong debug/.

Trước đây mỗi file debug tự chèn ``sys.path.insert(0, ".")`` (chỉ đúng khi
chạy script từ đúng thư mục gốc project) và tự gọi lại ``load_config()`` +
khởi tạo ``InventoryPipeline`` riêng. Module này thay thế cả hai:

    - ``PROJECT_ROOT``: đường dẫn tuyệt đối tới thư mục gốc project, luôn
      đúng bất kể bạn chạy script từ đâu (suy ra từ vị trí file này, không
      phụ thuộc thư mục làm việc hiện tại).
    - ``get_config()`` / ``get_pipeline()``: cached, chỉ load model một lần
      dù bạn gọi nhiều lần trong cùng 1 script.

Cách dùng trong mọi file debug/*.py::

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from debug._shared.bootstrap import PROJECT_ROOT, get_config, get_pipeline
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

# Thư mục gốc project = thư mục cha của debug/ (tức cha của _shared/'s parent)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@lru_cache(maxsize=None)
def get_config(config_path: str | None = None):
    """Load AppConfig (cached). Gọi lại với config_path khác sẽ load lại."""
    from src.core.config import load_config

    return load_config(config_path)


@lru_cache(maxsize=None)
def get_pipeline(config_path: str | None = None):
    """Khởi tạo InventoryPipeline (cached — chỉ load model 1 lần / config)."""
    from src.pipeline.pipeline import InventoryPipeline

    return InventoryPipeline(get_config(config_path))


_CATALOG_CACHE: dict[int, object] = {}


def get_catalog(config):
    """Catalog repository theo ``catalog.source`` của config (thay cho ``catalog.id_mapping`` cũ)."""
    from src.catalog.factory import open_catalog_repository

    key = id(config)
    if key not in _CATALOG_CACHE:
        _CATALOG_CACHE[key] = open_catalog_repository(config)
    return _CATALOG_CACHE[key]


def gallery_folder_of(config, product_id) -> str | None:
    """Thư mục gallery của một product_id, hoặc None nếu SKU không có/không có thư mục."""
    product = get_catalog(config).get_product(str(product_id))
    return product.get("gallery_folder") if product else None


def resolve_data_path(config, relative_key: str) -> Path:
    """Lấy 1 đường dẫn data từ config.paths theo tên field, đã resolve tuyệt đối.

    Ví dụ: resolve_data_path(cfg, "gallery_dir") -> Path tuyệt đối tới data/gallery.
    """
    relative_value = getattr(config.paths, relative_key)
    return config.resolve_path(relative_value)
