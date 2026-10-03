"""04_retrieval_selfcheck.py — Debug giai đoạn ⑤ RETRIEVAL (src/retrieval/retriever.py).

Trước đây (debug_retrieval.py): với mỗi ảnh gallery, đưa lại chính nó vào
Retriever và xem top-1 trả về gì. NHƯNG biến ``expected_id`` bị bỏ trống
(``None``) — status "OK/FAIL" chỉ dựa vào ngưỡng similarity > 0.9 chứ
KHÔNG đối chiếu đúng SKU thật. Bản này tra ``expected_id`` thật qua
``CatalogRepository.folder_to_product_id()`` (tên thư mục gallery -> product_id nội bộ),
nên "self-retrieval accuracy" phản ánh đúng: mỗi ảnh trong gallery có tự
tìm lại đúng SKU của chính nó không (sanity check tối thiểu — nếu cái này
còn sai thì đừng kỳ vọng retrieval đúng trên ảnh thật).

Cách dùng:
    python debug/04_retrieval_selfcheck.py
    python debug/04_retrieval_selfcheck.py --min-similarity 0.85
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from debug._shared.bootstrap import get_catalog, get_config, resolve_data_path
from debug._shared.formatting import format_number
from debug._shared.io_utils import list_images, load_bgr


def _reverse_id_mapping(cfg) -> dict[str, str]:
    """folder_name -> product_id (lấy từ catalog repository)."""
    return get_catalog(cfg).folder_to_product_id()


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-retrieval sanity check trên toàn bộ gallery.")
    parser.add_argument("--min-similarity", type=float, default=0.90, help="Ngưỡng similarity coi là OK khi product_id khớp (mặc định 0.90).")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    folder_to_id = _reverse_id_mapping(cfg)
    if not folder_to_id:
        print("[WARN] catalog không có SKU nào gắn thư mục gallery -> không tra được expected_id, mọi kết quả sẽ là UNKNOWN_SKU.")

    from src.models.models import BoundingBox, CropImage
    from src.core.utils import generate_id
    from src.retrieval.retriever import Retriever

    retriever = Retriever(cfg)
    gallery_dir = resolve_data_path(cfg, "gallery_dir")

    hits, near_misses, total = 0, 0, 0
    failures: list[str] = []

    for product_dir in sorted(p for p in gallery_dir.iterdir() if p.is_dir()):
        expected_id = folder_to_id.get(product_dir.name)
        for image_path in list_images(product_dir):
            total += 1
            image = load_bgr(image_path)
            crop = CropImage(
                crop_id=generate_id(), image_id="selfcheck",
                image_array=image, raw_image_array=image,
                source_bbox=BoundingBox(0, 0, image.shape[1], image.shape[0]),
                detection_confidence=1.0, detection_index=0,
            )
            result = retriever.retrieve(crop)
            top1 = result.top_candidate

            if expected_id is None:
                status = "UNKNOWN_SKU"
            elif top1 is not None and top1.product_id == expected_id:
                status = "OK"
                hits += 1
            elif top1 is not None and top1.similarity_score >= args.min_similarity:
                status = "NEAR (đúng similarity cao nhưng khác SKU!)"
                near_misses += 1
            else:
                status = "FAIL"

            line = (
                f"{product_dir.name:40.40s} ({image_path.name:20.20s}) "
                f"expected={expected_id!s:>4} top1={top1.product_id if top1 else None!s:>4} "
                f"sim={format_number(top1.similarity_score if top1 else None, 3)} [{status}]"
            )
            print(line)
            if status not in ("OK", "UNKNOWN_SKU"):
                failures.append(line)

    print("\n" + "=" * 90)
    print(f"Self-retrieval accuracy: {hits}/{total} ({format_number(100 * hits / total if total else 0, 1)}%)")
    print(f"Near-miss (similarity cao nhưng sai SKU — dấu hiệu 2 SKU dễ nhầm): {near_misses}")
    if failures:
        print(f"\n{len(failures)} trường hợp SAI/NEAR:")
        for line in failures:
            print(f"  {line}")


if __name__ == "__main__":
    main()
