"""05_retrieval_embedding_pairs.py — Debug backend embedding của giai đoạn
⑤ RETRIEVAL (engine/retrieval/backends/siglip2.py), ở mức THẤP HƠN
Retriever/FAISS: đo cosine similarity thô giữa từng CẶP ảnh gallery đã
biết trước quan hệ (same/similar/different SKU), để hiệu chỉnh trực giác
"ngưỡng similarity bao nhiêu là hợp lý" trước khi đụng tới
`decision.similarity_threshold` trong config.yaml.

Khác biệt so với 04_retrieval_selfcheck.py: file đó dùng `Retriever` +
FAISS index đã build (kiểm tra pipeline tra cứu đúng SKU không); file này
dùng thẳng `Siglip2Backend.embed()` (kiểm tra bản thân không gian embedding
có tách biệt tốt 3 nhóm same/similar/different không, độc lập với FAISS).

TRƯỚC ĐÂY (debug2.py): danh sách cặp ảnh test bị hardcode cứng trong code,
kèm path tuyệt đối máy cá nhân (`G:/VsCode/...`). Nay danh sách cặp nằm ở
`debug/_config/embedding_pairs.json` — sửa/thêm cặp test không cần đụng
code — và đường dẫn gallery lấy từ `config.yaml` như mọi nơi khác.

Cách dùng:
    python debug/05_retrieval_embedding_pairs.py
    python debug/05_retrieval_embedding_pairs.py --pairs-file debug/_config/my_pairs.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from debug._shared.bootstrap import get_config, resolve_data_path
from debug._shared.io_utils import load_bgr

_DEFAULT_PAIRS_FILE = Path(__file__).resolve().parent / "_config" / "embedding_pairs.json"

_GROUP_NAMES = {
    "same": "CÙNG SẢN PHẨM",
    "similar": "SẢN PHẨM TƯƠNG TỰ",
    "different": "SẢN PHẨM HOÀN TOÀN KHÁC",
}


def _embed(backend, image_path: Path) -> np.ndarray:
    image = load_bgr(image_path)
    embedding = np.asarray(backend.embed(image), dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(embedding)
    if norm == 0:
        raise ValueError(f"Embedding norm = 0: {image_path}")
    return embedding / norm


def main() -> None:
    parser = argparse.ArgumentParser(description="So sánh cosine similarity theo cặp ảnh gallery đã biết quan hệ.")
    parser.add_argument("--pairs-file", type=str, default=str(_DEFAULT_PAIRS_FILE))
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    cfg = get_config(args.config)
    gallery_dir = resolve_data_path(cfg, "gallery_dir")

    with open(args.pairs_file, "r", encoding="utf-8") as f:
        pairs = json.load(f)

    from engine.retrieval.backends.siglip2 import Siglip2Backend

    backend = Siglip2Backend(cfg.retrieval)

    results: dict[str, list[float]] = {"same": [], "similar": [], "different": []}

    for i, pair in enumerate(pairs, start=1):
        img1 = gallery_dir / pair["product1"] / pair["image1"]
        img2 = gallery_dir / pair["product2"] / pair["image2"]
        pair_type = pair["type"]

        print("\n" + "=" * 80)
        print(f"PAIR {i} [{pair_type}] — {pair.get('note', '')}")
        print(f"  {pair['product1']} / {pair['image1']}")
        print(f"  {pair['product2']} / {pair['image2']}")

        if not img1.is_file():
            print(f"[SKIP] Không tìm thấy: {img1}")
            continue
        if not img2.is_file():
            print(f"[SKIP] Không tìm thấy: {img2}")
            continue

        similarity = float(np.dot(_embed(backend, img1), _embed(backend, img2)))
        results.setdefault(pair_type, []).append(similarity)
        print(f"  Similarity: {similarity:.4f}")

    print("\n" + "=" * 80)
    print("THỐNG KÊ THEO NHÓM")
    print("=" * 80)
    for group, values in results.items():
        label = _GROUP_NAMES.get(group, group)
        print(f"\n[{label}]")
        if not values:
            print("  Không có dữ liệu")
            continue
        arr = np.array(values)
        print(f"  Số cặp: {len(arr)}   Mean: {arr.mean():.4f}   Min: {arr.min():.4f}   Max: {arr.max():.4f}   Std: {arr.std():.4f}")

    print("\n" + "=" * 80)
    print("Gợi ý: decision.similarity_threshold nên nằm giữa mean('different') và mean('same'/'similar').")
    print(f"       Giá trị hiện tại trong config: {cfg.decision.similarity_threshold}")


if __name__ == "__main__":
    main()
