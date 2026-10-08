"""Đo thời gian và bộ nhớ GPU của TỪNG BƯỚC pipeline, lặp lại nhiều lần trên vài ảnh cố định.

Lượt validate 30 phút cho số đo thời gian dao động tới ~40% trên cùng khối lượng việc, nên không
chứng minh được cải thiện tốc độ. Bài đo này chạy vài phút, lặp lại được, và tách riêng từng bước:

    python scripts/bench_stages.py                              # 4 ảnh benchmark đầu, 3 vòng
    python scripts/bench_stages.py --images data/benchmark/images --limit 6 --repeats 5
    python scripts/bench_stages.py --config configs/config.exp.yaml --tag nosam2

Chạy từ backend/, cần GPU trống (tắt web nhận diện thật trước). Không sửa gì trong engine: script
bọc các phương thức của pipeline đã nạp để bấm giờ. Trước và sau mỗi lần gọi có
``torch.cuda.synchronize()`` nên thời gian là của đúng bước đó (GPU chạy bất đồng bộ).

Đầu ra: bảng trên màn hình + ``data/experiments/bench_<tag>_<giờ>.json``.

Cách đọc bộ nhớ:
    allocated  bộ nhớ PyTorch thật sự đang dùng cho tensor
    reserved   bộ nhớ PyTorch đã xin của driver (allocated + phần giữ đệm, chưa trả lại)
    peak       đỉnh allocated trong lúc bước đó chạy
``nvidia-smi`` thấy xấp xỉ reserved cộng phần của driver, nên card có thể "đầy" trong khi
allocated còn thấp.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MB = 1024 * 1024


class Probe:
    """Bấm giờ và ghi bộ nhớ GPU quanh một lời gọi."""

    def __init__(self) -> None:
        import torch

        self.torch = torch
        self.cuda = torch.cuda.is_available()
        self.calls: list[dict] = []
        self.context: dict = {}

    def memory(self) -> dict:
        if not self.cuda:
            return {"allocated_mb": 0.0, "reserved_mb": 0.0}
        return {
            "allocated_mb": round(self.torch.cuda.memory_allocated() / MB, 1),
            "reserved_mb": round(self.torch.cuda.memory_reserved() / MB, 1),
        }

    def wrap(self, owner: object, method: str, stage: str) -> None:
        original = getattr(owner, method)

        def timed(*args, **kwargs):
            if self.cuda:
                self.torch.cuda.synchronize()
                self.torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                if self.cuda:
                    self.torch.cuda.synchronize()
                elapsed = (time.perf_counter() - started) * 1000
                peak = self.torch.cuda.max_memory_allocated() / MB if self.cuda else 0.0
                self.calls.append({"stage": stage, "ms": round(elapsed, 2), "peak_mb": round(peak, 1), **self.memory(), **self.context})

        setattr(owner, method, timed)


def measure_loading(probe: Probe, config) -> tuple[object, list[dict]]:
    """Nạp pipeline, ghi bộ nhớ GPU tăng thêm sau khi dựng từng thành phần."""
    import engine.pipeline.pipeline as pipeline_module
    from engine.inference.infer import InferenceRunner

    loaded: list[dict] = []
    originals = {}

    def tracked(name: str):
        cls = originals[name]

        def build(*args, **kwargs):
            before = probe.memory()
            started = time.perf_counter()
            instance = cls(*args, **kwargs)
            after = probe.memory()
            loaded.append(
                {
                    "component": name,
                    "seconds": round(time.perf_counter() - started, 1),
                    "allocated_delta_mb": round(after["allocated_mb"] - before["allocated_mb"], 1),
                    "allocated_after_mb": after["allocated_mb"],
                    "reserved_after_mb": after["reserved_mb"],
                }
            )
            return instance

        return build

    for name in ("Detector", "Refiner", "Retriever", "PluginManager"):
        originals[name] = getattr(pipeline_module, name)
        setattr(pipeline_module, name, tracked(name))
    try:
        runner = InferenceRunner(config)
    finally:
        for name, cls in originals.items():
            setattr(pipeline_module, name, cls)
    return runner, loaded


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


def summarize(calls: list[dict], warm_from: int) -> list[dict]:
    """Thống kê theo bước, chỉ tính các vòng từ `warm_from` trở đi (bỏ vòng nguội)."""
    by_stage: dict[str, list[dict]] = defaultdict(list)
    for call in calls:
        if call["repeat"] >= warm_from:
            by_stage[call["stage"]].append(call)
    images = {(c["repeat"], c["image"]) for c in calls if c["repeat"] >= warm_from}
    rows = []
    for stage, items in by_stage.items():
        ms = [c["ms"] for c in items]
        rows.append(
            {
                "stage": stage,
                "calls": len(items),
                "median_ms": round(statistics.median(ms), 1),
                "p10_ms": round(percentile(ms, 0.1), 1),
                "p90_ms": round(percentile(ms, 0.9), 1),
                "max_ms": round(max(ms), 1),
                "per_image_s": round(sum(ms) / 1000 / max(1, len(images)), 2),
                "peak_mb": round(max(c["peak_mb"] for c in items), 0),
            }
        )
    return sorted(rows, key=lambda row: -row["per_image_s"])


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Đo thời gian + bộ nhớ GPU từng bước pipeline, lặp lại được.")
    parser.add_argument("--images", type=Path, default=Path("data/benchmark/images"), help="thư mục ảnh (mặc định data/benchmark/images)")
    parser.add_argument("--limit", type=int, default=4, help="số ảnh đầu tiên (theo tên) đem đo")
    parser.add_argument("--repeats", type=int, default=3, help="số vòng lặp qua toàn bộ ảnh; vòng 0 là vòng nguội")
    parser.add_argument("--config", type=Path, help="file config của pipeline (mặc định configs/config.yaml)")
    parser.add_argument("--tag", default="base", help="nhãn của lần đo, vào tên file kết quả")
    parser.add_argument("--out", type=Path, default=Path("data/experiments"), help="thư mục ghi file JSON")
    args = parser.parse_args(argv)

    if args.repeats < 2:
        print("LỖI: cần ít nhất 2 vòng (vòng 0 là vòng nguội, không tính vào thống kê)", file=sys.stderr)
        return 2
    photos = sorted(p for p in args.images.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)[: args.limit] if args.images.is_dir() else []
    if not photos:
        print(f"LỖI: không có ảnh trong {args.images}", file=sys.stderr)
        return 2

    from engine.core.config import build_config, load_config
    from engine.core.utils import generate_id, load_image_bgr
    from engine.models.models import ImageData

    probe = Probe()
    config = build_config(args.config) if args.config else load_config()
    print(f"Nạp pipeline ({args.config or 'configs/config.yaml'}) ...")
    runner, loaded = measure_loading(probe, config)
    pipeline = runner.pipeline

    print("\n== Bộ nhớ GPU sau khi nạp từng thành phần ==")
    print(f"{'thành phần':<15}{'nạp (s)':>9}{'tăng thêm MB':>14}{'allocated MB':>14}{'reserved MB':>13}")
    for row in loaded:
        print(f"{row['component']:<15}{row['seconds']:>9}{row['allocated_delta_mb']:>14}{row['allocated_after_mb']:>14}{row['reserved_after_mb']:>13}")

    probe.wrap(pipeline._detector, "detect", "detect")
    probe.wrap(pipeline, "_refine", "refine (SAM2)")
    probe.wrap(pipeline._cropper, "crop", "crop")
    probe.wrap(pipeline._retriever, "retrieve", "retrieve")
    probe.wrap(pipeline._reranker, "rerank", "rerank")
    for plugin in pipeline._plugin_manager._plugins:
        probe.wrap(plugin, "run", f"plugin:{plugin.name}")

    per_image: list[dict] = []
    for repeat in range(args.repeats):
        for photo in photos:
            array = load_image_bgr(photo)
            height, width = array.shape[:2]
            data = ImageData(image_id=generate_id(prefix="img_"), source_path=str(photo), image_array=array, width=width, height=height)
            probe.context = {"repeat": repeat, "image": photo.name}
            if probe.cuda:
                probe.torch.cuda.synchronize()
            started = time.perf_counter()
            result = pipeline.run(data)
            if probe.cuda:
                probe.torch.cuda.synchronize()
            seconds = time.perf_counter() - started
            per_image.append({"repeat": repeat, "image": photo.name, "seconds": round(seconds, 2), "detected": int(result.detected_count), "items": len(result.items), **probe.memory()})
            print(f"vòng {repeat} {photo.name[:28]:<28} {seconds:6.1f} s  {result.detected_count:2d} vật  allocated {per_image[-1]['allocated_mb']:.0f} MB  reserved {per_image[-1]['reserved_mb']:.0f} MB")

    rows = summarize(probe.calls, warm_from=1)
    print("\n== Từng bước (bỏ vòng 0), xếp theo thời gian mỗi ảnh ==")
    print(f"{'bước':<16}{'lần gọi':>8}{'median ms':>11}{'p10':>9}{'p90':>9}{'max':>9}{'s/ảnh':>8}{'đỉnh MB':>9}")
    for row in rows:
        print(f"{row['stage']:<16}{row['calls']:>8}{row['median_ms']:>11}{row['p10_ms']:>9}{row['p90_ms']:>9}{row['max_ms']:>9}{row['per_image_s']:>8}{row['peak_mb']:>9.0f}")

    print("\n== Mỗi ảnh qua các vòng (giây): cùng ảnh, cùng việc — chênh nhau là nhiễu ==")
    for photo in photos:
        times = [entry["seconds"] for entry in per_image if entry["image"] == photo.name]
        warm = times[1:]
        spread = (max(warm) - min(warm)) / statistics.mean(warm) * 100 if len(warm) > 1 else 0.0
        print(f"{photo.name[:28]:<28} " + "  ".join(f"{t:6.1f}" for t in times) + f"   (vòng nóng lệch {spread:.0f}%)")
    warm_totals = [sum(e["seconds"] for e in per_image if e["repeat"] == r) for r in range(1, args.repeats)]
    print(f"Tổng mỗi vòng nóng: {', '.join(f'{t:.1f} s' for t in warm_totals)}; trung bình {statistics.mean(warm_totals) / len(photos):.1f} s/ảnh")
    if probe.cuda:
        print(f"Bộ nhớ cuối: allocated {probe.memory()['allocated_mb']:.0f} MB, reserved {probe.memory()['reserved_mb']:.0f} MB")

    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"bench_{args.tag}_{datetime.now().strftime('%m%d-%H%M%S')}.json"
    payload = {"tag": args.tag, "config": str(args.config or "configs/config.yaml"), "images": [p.name for p in photos], "repeats": args.repeats, "loading": loaded, "stages": rows, "per_image": per_image, "calls": probe.calls}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nĐã ghi {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
