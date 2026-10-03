"""Hàm format/parse an toàn dùng chung cho debug/.

Trước đây các hàm này (``safe_float``, ``safe_str``/``safe_value``,
``format_number``, ``wrap_text``) bị chép tay gần như y hệt ở
``debug_val_product_viewer.py``, ``debug_reranker.py``, ``debug_ocr.py``.
Gom về đây để sửa 1 chỗ, dùng ở mọi nơi.
"""

from __future__ import annotations

import textwrap
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """Ép kiểu float an toàn — trả về default nếu None/NaN/không hợp lệ."""
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result:  # NaN check không cần import math
        return default
    return result


def safe_int(value: Any, default: int = 0) -> int:
    """Ép kiểu int an toàn (qua float trước để chấp nhận '3.0', '3')."""
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_value(value: Any, default: str = "N/A") -> Any:
    """Trả về default nếu value là None hoặc NaN (thường gặp khi đọc pandas.DataFrame)."""
    if value is None:
        return default
    try:
        if value != value:  # NaN
            return default
    except TypeError:
        pass
    return value


def safe_str(value: Any, default: str = "N/A") -> str:
    """Như safe_value nhưng luôn trả về str."""
    return str(safe_value(value, default))


def format_number(value: Any, ndigits: int = 2, default: str = "N/A") -> str:
    """Format số với số chữ số thập phân cố định, an toàn với None/NaN."""
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return f"{number:.{ndigits}f}"


def parse_bool(value: Any, default: bool = False) -> bool:
    """Parse bool từ str/bool/số (hữu ích khi đọc cột CSV kiểu 'True'/'False')."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n", "", "nan", "none"):
        return False
    return default


def wrap_text(text: str, width: int = 30) -> str:
    """Bọc dòng chữ dài (dùng cho nhãn/annotation trên ảnh)."""
    if not text:
        return ""
    return "\n".join(textwrap.wrap(text, width=width)) or text
