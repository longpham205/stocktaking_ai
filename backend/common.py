"""Kiểu và hàm dùng chung của tầng nghiệp vụ web (lỗi API, phiên đăng nhập, kiểm tra dữ liệu vào)."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

MAX_QTY = 999
MAX_PRICE = 100_000_000
_BARCODE_RE = re.compile(r"^[0-9A-Za-z\-]{4,32}$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{3,32}$")
_COLOR_CODE_RE = re.compile(r"^[A-Z0-9_\-]{2,20}$")
EVIDENCE_FIELDS = ("ocr_keywords", "color_code", "force_evidence", "confusable_with")
CONFIRM_TEXT = "Tôi hiểu thay đổi này ảnh hưởng độ chính xác AI"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", **extra) -> None:
        super().__init__(message or code)
        self.status, self.code, self.message, self.extra = status, code, message or code, extra


def _err(status: int, code: str, message: str = "", **extra) -> ApiError:
    return ApiError(status, code, message, **extra)


@dataclass
class Session:
    user_id: int
    role: str
    shift_id: int
    username: str
    full_name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def fold(text: str) -> str:
    """Bỏ dấu tiếng Việt + hạ chữ thường để tìm kiếm không phân biệt dấu."""
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _as_int(value, name: str, lo: int = 0, hi: int = MAX_PRICE) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise _err(422, "VALIDATION_ERROR", f"'{name}' phải là số nguyên")
    v = int(value)
    if not lo <= v <= hi:
        raise _err(422, "VALIDATION_ERROR", f"'{name}' phải trong khoảng {lo}..{hi}")
    return v


def _evidence_json(evidence: dict | None) -> str | None:
    """Tuần tự hoá bằng chứng plugin an toàn: chấp nhận số numpy/đối tượng lạ, cắt nếu quá dài (không làm hỏng lượt chụp)."""
    if not evidence:
        return None

    def default(o):
        if hasattr(o, "tolist"):  # numpy: cả số đơn lẫn mảng (`.item()` lỗi với mảng nhiều phần tử)
            return o.tolist()
        if hasattr(o, "item"):
            return o.item()
        return str(o)
    try:
        text = json.dumps(evidence, default=default, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return text if len(text) <= 8000 else None
