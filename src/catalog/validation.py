"""Luật kiểm tra bằng chứng nhận diện khi LƯU catalog (migrate, admin).

Hàm thuần, không đụng DB: nhận dữ liệu dạng dict, trả ``ValidationReport``.
- ``errors``: chặn cứng, không được lưu.
- ``warnings``: được lưu nhưng phải báo (ví dụ ``color_code`` chưa có màu tham chiếu).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

FORCE_EVIDENCE_PLUGINS = frozenset({"ocr", "color", "barcode"})
KNOWN_EVIDENCE_TYPES = frozenset(
    {"force_evidence", "confusable_with", "ocr_keywords", "color_code", "disabled_plugins"}
)
_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        if self.errors:
            raise CatalogValidationError(self.errors)


class CatalogValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("Catalog không hợp lệ:\n- " + "\n- ".join(self.errors))


def normalize_ocr_token(raw: str) -> str:
    """Chuẩn hoá GIỐNG HỆT ``Reranker._normalize_text`` (chữ hoa, chỉ giữ chữ/số): "BE-203" -> "BE203".
    Từ khoá có dấu '-', '/', khoảng trắng... sẽ không bao giờ khớp nếu không chuẩn hoá cùng quy tắc."""
    return "".join(ch for ch in str(raw).upper().strip() if ch.isalnum())


def normalize_ocr_keywords(values: Iterable[Any], min_length: int) -> list[str]:
    """Chuẩn hoá như phía OCR (``normalize_ocr_token``), bỏ trùng (giữ thứ tự). Lỗi nếu token ngắn hơn ``min_length``."""
    out: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError(f"Từ khoá OCR phải là chuỗi, nhận {raw!r}")
        token = normalize_ocr_token(raw)
        if not token:
            continue
        if len(token) < min_length:
            raise ValueError(f"Từ khoá OCR '{token}' ngắn hơn {min_length} ký tự")
        if token not in out:
            out.append(token)
    return out


def parse_hex(hex_value: str) -> tuple[int, int, int]:
    m = _HEX_RE.match(str(hex_value).strip())
    if not m:
        raise ValueError(f"Mã hex không hợp lệ: {hex_value!r} (cần dạng #RRGGBB)")
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    for v in (r, g, b):
        if not isinstance(v, int) or not 0 <= v <= 255:
            raise ValueError(f"Giá trị RGB phải là số nguyên 0..255, nhận {(r, g, b)}")
    return f"#{r:02X}{g:02X}{b:02X}"


def validate_catalog(
    product_ids: Iterable[str],
    evidence: Mapping[str, Mapping[str, Any]],
    color_codes: Iterable[str],
    ocr_min_length: int,
) -> ValidationReport:
    """Kiểm toàn bộ bằng chứng của catalog (sau khi áp thay đổi định lưu)."""
    report = ValidationReport()
    ids = {str(p) for p in product_ids}
    colors = set(color_codes)

    for pid, types in evidence.items():
        if pid not in ids:
            report.errors.append(f"Bằng chứng của SKU {pid} nhưng SKU không tồn tại")
            continue
        for etype, value in types.items():
            if etype not in KNOWN_EVIDENCE_TYPES:
                report.warnings.append(f"SKU {pid}: loại bằng chứng chưa biết '{etype}' (được lưu, chưa plugin nào dùng)")
            elif etype == "force_evidence":
                if not _is_str_list(value):
                    report.errors.append(f"SKU {pid}: force_evidence phải là danh sách chuỗi")
                else:
                    bad = sorted(set(value) - FORCE_EVIDENCE_PLUGINS)
                    if bad:
                        report.errors.append(f"SKU {pid}: force_evidence có plugin lạ {bad}")
            elif etype == "confusable_with":
                if not _is_str_list(value):
                    report.errors.append(f"SKU {pid}: confusable_with phải là danh sách product_id dạng chuỗi")
                    continue
                for other in value:
                    if other == pid:
                        report.errors.append(f"SKU {pid}: confusable_with trỏ vào chính nó")
                    elif other not in ids:
                        report.errors.append(f"SKU {pid}: confusable_with trỏ tới SKU {other} không tồn tại")
                    elif pid not in (evidence.get(other, {}).get("confusable_with") or ()):
                        report.errors.append(f"Cặp dễ nhầm {pid}/{other} không hai chiều")
            elif etype == "ocr_keywords":
                if not _is_str_list(value):
                    report.errors.append(f"SKU {pid}: ocr_keywords phải là danh sách chuỗi")
                    continue
                try:
                    normalized = normalize_ocr_keywords(value, ocr_min_length)
                except ValueError as exc:
                    report.errors.append(f"SKU {pid}: {exc}")
                    continue
                if normalized != list(value):
                    report.errors.append(f"SKU {pid}: ocr_keywords chưa chuẩn hoá (cần {normalized})")
            elif etype == "color_code":
                if not isinstance(value, str) or not value.strip():
                    report.errors.append(f"SKU {pid}: color_code phải là chuỗi không rỗng")
                elif value not in colors:
                    report.warnings.append(f"SKU {pid}: color_code {value} chưa có màu tham chiếu (điểm màu = 0)")
            elif etype == "disabled_plugins":
                if not _is_str_list(value):
                    report.errors.append(f"SKU {pid}: disabled_plugins phải là danh sách chuỗi")

    # Cặp dễ nhầm mà một bên bắt buộc OCR: cả hai phải có từ khoá và không trùng token.
    seen: set[frozenset[str]] = set()
    for pid, types in evidence.items():
        for other in types.get("confusable_with") or ():
            pair = frozenset((pid, other))
            if pair in seen or other not in ids or other == pid:
                continue
            seen.add(pair)
            a, b = sorted(pair, key=lambda x: (len(x), x))
            ev_a, ev_b = evidence.get(a, {}), evidence.get(b, {})
            needs_ocr = "ocr" in (ev_a.get("force_evidence") or ()) or "ocr" in (ev_b.get("force_evidence") or ())
            if not needs_ocr:
                continue
            kw_a = set(ev_a.get("ocr_keywords") or ())
            kw_b = set(ev_b.get("ocr_keywords") or ())
            if not kw_a or not kw_b:
                report.errors.append(f"Cặp dễ nhầm {a}/{b} bắt buộc OCR nhưng thiếu ocr_keywords")
            elif kw_a & kw_b:
                report.errors.append(f"Cặp dễ nhầm {a}/{b} có từ khoá OCR trùng {sorted(kw_a & kw_b)}")
    return report


def _is_str_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)


__all__ = [
    "FORCE_EVIDENCE_PLUGINS",
    "KNOWN_EVIDENCE_TYPES",
    "ValidationReport",
    "CatalogValidationError",
    "normalize_ocr_keywords",
    "normalize_ocr_token",
    "parse_hex",
    "rgb_to_hex",
    "validate_catalog",
]
