"""Registry thiết lập NÂNG CAO của pipeline hiển thị trên web (quyết định D12): thêm một thông số = thêm một dòng.

Mức (tier):
- ``readonly``: chỉ hiển thị (model, backend, thiết bị, kích thước vector...). Đổi trong file config + chạy lại cổng G.
- ``reload``  : ảnh hưởng nhận diện; sửa trên web cần mật khẩu nâng cao + bấm Áp dụng -> nạp lại pipeline
               (ngừng nhận diện ~30–60 giây). Ghi đè lưu ở bảng ``config_overrides``, YAML giữ giá trị gốc.
Thiết lập "hiệu lực ngay" (ngưỡng theo request, cài đặt POS) nằm ở tab Cài đặt (``App.update_settings``).

Khoảng giá trị ở đây là khoảng AN TOÀN cho web (chặt hơn schema pydantic); toàn bộ config sau khi ghép vẫn
được validate lại bằng ``AppConfig``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

READONLY, RELOAD = "readonly", "reload"


@dataclass(frozen=True)
class ConfigKey:
    key: str
    label: str
    group: str
    tier: str
    type: str  # float | int | bool | str
    help: str = ""
    min: float | None = None
    max: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _f(key, label, group, lo, hi, help=""):
    return ConfigKey(key, label, group, RELOAD, "float", help, lo, hi)


def _i(key, label, group, lo, hi, help=""):
    return ConfigKey(key, label, group, RELOAD, "int", help, lo, hi)


def _b(key, label, group, help=""):
    return ConfigKey(key, label, group, RELOAD, "bool", help)


def _ro(key, label, group, help=""):
    return ConfigKey(key, label, group, READONLY, "str", help)


REGISTRY: tuple[ConfigKey, ...] = (
    # ---- chỉ xem
    _ro("app.device", "Thiết bị chạy chung", "Hệ thống"),
    _ro("catalog.source", "Nguồn catalog", "Hệ thống"),
    _ro("detection.backend", "Detector", "Mô hình", "Đổi model cần build/kiểm định lại — sửa file config."),
    _ro("detection.rf_detr.variant", "Biến thể RF-DETR", "Mô hình"),
    _ro("refinement.backend", "Tinh chỉnh vùng (SAM2)", "Mô hình"),
    _ro("retrieval.backend", "Truy hồi ảnh", "Mô hình"),
    _ro("retrieval.siglip2.model_name", "Model SigLIP2", "Mô hình"),
    _ro("retrieval.embedding_dim", "Số chiều vector", "Mô hình", "Đổi phải build lại index FAISS."),
    # ---- cần áp dụng (nạp lại pipeline)
    _f("detection.confidence_threshold", "Ngưỡng tin cậy detector", "Phát hiện", 0.05, 0.95,
       "Thấp: bắt nhiều vật hơn nhưng dễ nhận nhầm nền; cao: bỏ sót vật mờ/nhỏ."),
    _i("detection.max_detections", "Số vật tối đa mỗi ảnh", "Phát hiện", 1, 200),
    _b("refinement.enabled", "Bật tinh chỉnh vùng chồng lấp (SAM2)", "Phát hiện", "Tắt: nhanh hơn, vật chồng lấp kém chính xác hơn."),
    _i("retrieval.top_k", "Số ứng viên truy hồi (Top-K)", "Quyết định", 1, 20),
    _f("decision.uncertain_band", "Biên 'không chắc chắn'", "Quyết định", 0.0, 0.5),
    _f("decision.detection_weight", "Trọng số độ tin cậy detector", "Quyết định", 0.0, 1.0),
    _f("decision.similarity_weight", "Trọng số độ giống (retrieval)", "Quyết định", 0.0, 1.0),
    _i("decision.ambiguous_top_n", "Số ứng viên xét 'mơ hồ'", "Quyết định", 2, 10),
    _f("decision.ambiguous_margin", "Chênh lệch tối thiểu để không 'mơ hồ'", "Quyết định", 0.0, 0.5),
    _b("plugins.enabled", "Bật plugin bằng chứng (OCR/màu/barcode)", "Plugin"),
    _b("plugins.ocr.enabled", "Bật OCR", "Plugin", "OCR chậm nhất (~3 giây/vật trên GPU này)."),
    _b("plugins.color.enabled", "Bật đo màu", "Plugin"),
    _b("plugins.barcode.enabled", "Bật đọc mã vạch", "Plugin"),
    _f("rerank.ocr.weight", "Trọng số bằng chứng OCR", "Hợp nhất bằng chứng", 0.0, 1.0),
    _f("rerank.color.weight", "Trọng số bằng chứng màu", "Hợp nhất bằng chứng", 0.0, 1.0),
    _f("rerank.barcode.weight", "Trọng số bằng chứng mã vạch", "Hợp nhất bằng chứng", 0.0, 1.0),
    _f("rerank.color.delta_e_strong", "ΔE 'khớp màu mạnh'", "Hợp nhất bằng chứng", 1.0, 30.0),
    _f("rerank.color.delta_e_weak", "ΔE 'khớp màu yếu'", "Hợp nhất bằng chứng", 2.0, 60.0),
    _f("rerank.retrieval_protection.min_switch_margin", "Chênh lệch tối thiểu để đổi Top-1", "Hợp nhất bằng chứng", 0.0, 0.5),
    _i("rerank.confusable_min_agreeing_plugins", "Số plugin phải đồng ý (cặp dễ nhầm)", "Hợp nhất bằng chứng", 1, 3),
)
BY_KEY = {k.key: k for k in REGISTRY}


def get_path(obj: Any, dotted: str) -> Any:
    """Lấy giá trị theo đường dẫn chấm từ dict (YAML thô) hoặc model pydantic."""
    cur = obj
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def normalize_value(entry: ConfigKey, value: Any) -> Any:
    """Kiểm kiểu + khoảng an toàn. Raise ValueError (thông báo tiếng Việt)."""
    if entry.tier != RELOAD:
        raise ValueError(f"'{entry.label}' chỉ được xem, không sửa trên web")
    if entry.type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"'{entry.label}' phải là bật/tắt")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{entry.label}' phải là số")
    if entry.type == "int":
        if float(value) != int(value):
            raise ValueError(f"'{entry.label}' phải là số nguyên")
        value = int(value)
    else:
        value = float(value)
    if (entry.min is not None and value < entry.min) or (entry.max is not None and value > entry.max):
        raise ValueError(f"'{entry.label}' phải trong khoảng {entry.min:g}–{entry.max:g}")
    return value


__all__ = ["READONLY", "RELOAD", "ConfigKey", "REGISTRY", "BY_KEY", "get_path", "normalize_value"]
