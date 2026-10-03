"""Kiểm tra nhất quán catalog lúc khởi động (báo lỗi rõ, KHÔNG tự sửa).

Lỗi (chặn):
- ``product_id`` trong ``gallery_metadata.json`` (FAISS) không có trong catalog;
- số vector của index khác số dòng metadata; số chiều index khác ``embedding_dim``;
- ``category_id`` trong nhãn COCO benchmark không có trong catalog;
- tham chiếu ``confusable_with`` hỏng / bằng chứng vi phạm luật (``validation.validate_catalog``).
Cảnh báo: ``color_code`` chưa có màu tham chiếu; SKU đang hoạt động có thư mục nhưng chưa có vector.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.catalog.repository import BaseCatalogRepository
from src.catalog.validation import ValidationReport, validate_catalog


def check_catalog(
    repo: BaseCatalogRepository,
    *,
    ocr_min_length: int,
    gallery_metadata_path: str | Path | None = None,
    gallery_index_path: str | Path | None = None,
    embedding_dim: int | None = None,
    benchmark_labels: str | Path | None = None,
) -> ValidationReport:
    data = repo.data
    ids = set(data.products)
    report = validate_catalog(
        ids,
        {pid: dict(types) for pid, types in data.evidence.items()},
        data.colors.keys(),
        ocr_min_length,
    )

    meta_count = None
    if gallery_metadata_path is not None:
        path = Path(gallery_metadata_path)
        if not path.is_file():
            report.errors.append(f"Không thấy metadata gallery: {path}")
        else:
            rows = json.loads(path.read_text(encoding="utf-8"))
            meta_count = len(rows)
            index_ids = {str(r["product_id"]) for r in rows}
            unknown = sorted(index_ids - ids, key=lambda x: (len(x), x))
            if unknown:
                report.errors.append(f"Index FAISS có product_id không có trong catalog: {unknown}")
            no_vector = sorted(
                (pid for pid, p in data.products.items() if p.is_active and p.gallery_folder and pid not in index_ids),
                key=lambda x: (len(x), x),
            )
            if no_vector:
                report.warnings.append(f"SKU có thư mục gallery nhưng chưa có vector trong index: {no_vector}")

    if gallery_index_path is not None:
        path = Path(gallery_index_path)
        if not path.is_file():
            report.errors.append(f"Không thấy index FAISS: {path}")
        else:
            import faiss  # nạp muộn: chỉ cần khi kiểm index

            index = faiss.read_index(str(path))
            if embedding_dim is not None and index.d != embedding_dim:
                report.errors.append(f"Số chiều index FAISS = {index.d}, config embedding_dim = {embedding_dim}")
            if meta_count is not None and index.ntotal != meta_count:
                report.errors.append(f"Index FAISS có {index.ntotal} vector nhưng metadata có {meta_count} dòng")

    if benchmark_labels is not None:
        path = Path(benchmark_labels)
        if path.is_file():
            coco = json.loads(path.read_text(encoding="utf-8"))
            cats = sorted({int(a["category_id"]) for a in coco.get("annotations", [])})
            missing = [c for c in cats if str(c) not in ids]
            if missing:
                report.errors.append(f"category_id của benchmark không có trong catalog: {missing}")
    return report


__all__ = ["check_catalog"]
