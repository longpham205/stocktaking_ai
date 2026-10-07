"""Gán nhãn ảnh benchmark mới: pipeline đề xuất khung + sản phẩm, người duyệt sửa chỗ sai.

Hai bước, chạy từ backend/:

    # 1. Đề xuất (GPU; tắt web nhận diện thật trước, 4 GB không chứa được hai pipeline)
    python scripts/label_benchmark.py propose --images data/benchmark_inbox --out data/benchmark_new

    # 2. Duyệt trong cửa sổ (không cần GPU); --only thu gọn danh sách sản phẩm để chọn
    python scripts/label_benchmark.py review --dir data/benchmark_new [--only 21-39]

Kết quả là một thư mục benchmark đúng định dạng của ``python -m engine --mode validate``:

    data/benchmark_new/images/*.jpg
    data/benchmark_new/_annotations.coco.json     chỉ gồm các ảnh ĐÃ DUYỆT; category_id = product_id
    data/benchmark_new/_labels.json               trạng thái làm việc (đề xuất, sửa, ảnh nào đã duyệt)

Thư mục riêng, không đụng ``data/benchmark`` (baseline cũ vẫn so được). Đo trên bộ mới:
``python -m engine --mode validate --benchmark-dir data/benchmark_new``.

Cửa sổ duyệt:
    chạm khung để chọn · kéo trên vùng trống để vẽ khung mới · Delete xoá khung đang chọn
    kéo bên trong khung để dời nó · kéo viền hoặc góc của khung đang chọn để đổi kích thước
    cột bên phải liệt kê các khung của ảnh và nhãn của chúng (bấm một dòng để chọn khung đó);
    dấu ✔ = khung bạn đã sửa (gán nhãn, dời, đổi cỡ hoặc tự vẽ)
    gõ vào ô tìm để lọc sản phẩm, Enter hoặc nhấp đúp để gán cho khung đang chọn
    A = ảnh này đúng hết, sang ảnh sau (đánh dấu ĐÃ DUYỆT) · U bỏ duyệt · ←/→ chuyển ảnh · Esc thoát
Khung vàng = pipeline chưa chắc hoặc sản phẩm nằm ngoài --only, đỏ = chưa có sản phẩm (phải gán
hoặc xoá mới duyệt được).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
LABELS_NAME = "_labels.json"
COCO_NAME = "_annotations.coco.json"
MIN_BOX = 8  # pixel ảnh gốc: khung nhỏ hơn coi như bấm nhầm


# ---------------------------------------------------------------- trạng thái + xuất COCO (không cần GPU, không cần màn hình)


def load_labels(directory: Path) -> dict:
    path = directory / LABELS_NAME
    if not path.is_file():
        raise ValueError(f"Không thấy {path}: chạy bước 'propose' trước")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "images" not in data or "products" not in data:
        raise ValueError(f"{path} không đúng định dạng")
    return data


def save_labels(directory: Path, labels: dict) -> None:
    """Ghi trạng thái làm việc và xuất lại COCO (chỉ ảnh đã duyệt), cả hai qua file tạm."""
    for name, payload in ((LABELS_NAME, labels), (COCO_NAME, to_coco(labels))):
        tmp = directory / (name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(directory / name)


def clamp_box(box: list[float], width: int, height: int) -> list[float] | None:
    """Khung [x1, y1, x2, y2] nằm trong ảnh, toạ độ tăng dần; None nếu quá nhỏ."""
    x1, x2 = sorted((max(0.0, min(float(width), box[0])), max(0.0, min(float(width), box[2]))))
    y1, y2 = sorted((max(0.0, min(float(height), box[1])), max(0.0, min(float(height), box[3]))))
    if x2 - x1 < MIN_BOX or y2 - y1 < MIN_BOX:
        return None
    return [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]


def grab_handle(bbox: list[float], x: float, y: float, tolerance: float) -> str | None:
    """Phần của khung nằm dưới con trỏ: cạnh/góc ("n", "s", "w", "e", "nw", ...), "move" nếu ở bên
    trong, None nếu ở ngoài. `tolerance` là bề dày vùng bắt viền (pixel ảnh gốc)."""
    x1, y1, x2, y2 = bbox
    if not (x1 - tolerance <= x <= x2 + tolerance and y1 - tolerance <= y <= y2 + tolerance):
        return None
    vertical = min((abs(y - y1), "n"), (abs(y - y2), "s"))
    horizontal = min((abs(x - x1), "w"), (abs(x - x2), "e"))
    edges = (vertical[1] if vertical[0] <= tolerance else "") + (horizontal[1] if horizontal[0] <= tolerance else "")
    return edges or "move"


def drag_box(bbox: list[float], handle: str, dx: float, dy: float, width: int, height: int) -> list[float]:
    """Khung sau khi kéo `handle` đi (dx, dy): "move" dời cả khung (giữ kích thước), cạnh/góc thì
    đổi kích thước. Luôn nằm trong ảnh và không nhỏ hơn MIN_BOX."""
    x1, y1, x2, y2 = bbox
    if handle == "move":
        dx = max(-x1, min(width - x2, dx))
        dy = max(-y1, min(height - y2, dy))
        x1, y1, x2, y2 = x1 + dx, y1 + dy, x2 + dx, y2 + dy
    else:
        if "w" in handle:
            x1 = max(0.0, min(x2 - MIN_BOX, x1 + dx))
        if "e" in handle:
            x2 = min(float(width), max(x1 + MIN_BOX, x2 + dx))
        if "n" in handle:
            y1 = max(0.0, min(y2 - MIN_BOX, y1 + dy))
        if "s" in handle:
            y2 = min(float(height), max(y1 + MIN_BOX, y2 + dy))
    return [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]


def parse_only(text: str) -> set[str]:
    """"21-39" hoặc "21,24,29-32" -> tập mã sản phẩm (chuỗi)."""
    chosen: set[str] = set()
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        if not first.isdigit() or (last and not last.isdigit()):
            raise ValueError(f"--only không hiểu được '{part.strip()}': viết dạng 21-39 hoặc 21,24,29-32")
        chosen.update(str(pid) for pid in range(int(first), int(last or first) + 1))
    return chosen


def was_edited(box: dict) -> bool:
    """Khung đã qua tay người: gán nhãn, dời, đổi cỡ hoặc tự vẽ."""
    return bool(box.get("edited")) or box.get("source") == "manual"


def unlabeled(image: dict) -> int:
    return sum(1 for box in image["boxes"] if not box.get("product_id"))


def to_coco(labels: dict) -> dict:
    """COCO như benchmark hiện có: bbox [x, y, w, h], category_id = product_id (số)."""
    images, annotations, used = [], [], set()
    for image in labels["images"]:
        if not image.get("reviewed"):
            continue
        image_id = len(images)
        images.append({"id": image_id, "file_name": image["file_name"], "width": image["width"], "height": image["height"]})
        for box in image["boxes"]:
            x1, y1, x2, y2 = box["bbox"]
            category = int(box["product_id"])
            used.add(category)
            annotations.append(
                {
                    "id": len(annotations) + 1,
                    "image_id": image_id,
                    "category_id": category,
                    "bbox": [x1, y1, round(x2 - x1, 2), round(y2 - y1, 2)],
                    "area": round((x2 - x1) * (y2 - y1), 2),
                    "iscrowd": 0,
                    "segmentation": [],
                }
            )
    return {
        "info": {"description": "Gán nhãn bằng scripts/label_benchmark.py", "date_created": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        "licenses": [],
        "categories": [{"id": c, "name": str(c), "supercategory": "product"} for c in sorted(used)],
        "images": images,
        "annotations": annotations,
    }


def mark_reviewed(image: dict, reviewed: bool = True) -> None:
    if reviewed and unlabeled(image):
        raise ValueError(f"Còn {unlabeled(image)} khung chưa có sản phẩm: gán hoặc xoá trước khi duyệt")
    image["reviewed"] = reviewed


# ---------------------------------------------------------------- bước 1: đề xuất


def propose(images_dir: Path, out_dir: Path, config_path: Path | None) -> int:
    sources = sorted(p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not sources:
        print(f"LỖI: không có ảnh trong {images_dir}", file=sys.stderr)
        return 2
    existing: dict[str, dict] = {}
    if (out_dir / LABELS_NAME).is_file():
        existing = {image["file_name"]: image for image in load_labels(out_dir)["images"]}
    (out_dir / "images").mkdir(parents=True, exist_ok=True)

    # nhập muộn: bước duyệt và các test không cần torch. `python scripts/...` chỉ đưa scripts/ vào
    # sys.path, nên thêm backend/ để thấy gói `engine`.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from engine.catalog.factory import open_catalog_repository
    from engine.core.config import build_config, load_config
    from engine.inference.infer import InferenceRunner
    from PIL import Image, ImageOps

    config = build_config(config_path) if config_path else load_config()
    products = {pid: rec.to_dict().get("product_name") or f"SKU {pid}" for pid, rec in open_catalog_repository(config).products().items()}
    runner = InferenceRunner(config)

    images = []
    for index, src in enumerate(sources, 1):
        kept = existing.get(src.name)
        if kept is not None and (kept.get("reviewed") or kept.get("touched")):
            images.append(kept)  # đã có công người sửa: không ghi đè bằng đề xuất mới
            print(f"[{index}/{len(sources)}] {src.name}: giữ nhãn đã sửa")
            continue
        target = out_dir / "images" / src.name
        if not target.is_file():
            shutil.copy2(src, target)
        with Image.open(target) as opened:
            # kích thước SAU khi xoay theo EXIF: cùng hệ toạ độ với ảnh pipeline đọc (OpenCV tự xoay)
            width, height = ImageOps.exif_transpose(opened).size
        result = runner.run_single(str(target), persist=False)
        boxes = []
        for item in result.items:
            if item.status not in ("accepted", "uncertain"):
                continue
            bbox = clamp_box([item.bbox.x1, item.bbox.y1, item.bbox.x2, item.bbox.y2], width, height)
            if bbox:
                boxes.append({"bbox": bbox, "product_id": str(item.product_id), "source": item.status, "confidence": round(float(item.final_confidence), 3)})
        for rejected in result.rejected_bboxes:
            bbox = clamp_box([rejected.x1, rejected.y1, rejected.x2, rejected.y2], width, height)
            if bbox:
                boxes.append({"bbox": bbox, "product_id": None, "source": "rejected", "confidence": None})
        images.append({"file_name": src.name, "width": width, "height": height, "reviewed": False, "touched": False, "boxes": boxes})
        print(f"[{index}/{len(sources)}] {src.name}: {len(boxes)} khung ({unlabeled(images[-1])} chưa có sản phẩm)")

    save_labels(out_dir, {"products": products, "images": images})
    print(f"Đã ghi {out_dir / LABELS_NAME}. Bước tiếp: python scripts/label_benchmark.py review --dir {out_dir}")
    return 0


# ---------------------------------------------------------------- bước 2: duyệt

COLORS = {"ok": "#10b981", "unsure": "#f59e0b", "missing": "#ef4444"}
GRAB_PX = 7  # pixel màn hình: bề dày vùng bắt viền khung đang chọn
MOVE_PX = 4  # pixel màn hình: kéo ngắn hơn coi như chỉ bấm chọn
CURSORS = {"move": "fleur", "n": "sb_v_double_arrow", "s": "sb_v_double_arrow", "w": "sb_h_double_arrow", "e": "sb_h_double_arrow", "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw"}


def box_kind(box: dict, only: set[str] | None = None) -> str:
    if not box.get("product_id"):
        return "missing"
    if only is not None and box["product_id"] not in only:
        return "unsure"  # sản phẩm ngoài bộ đang gán: đáng xem lại
    return "unsure" if box.get("source") == "uncertain" else "ok"


def review(directory: Path, only: set[str] | None = None) -> int:
    import tkinter as tk
    from tkinter import messagebox

    from PIL import Image, ImageOps, ImageTk

    labels = load_labels(directory)
    images, products = labels["images"], labels["products"]
    if not images:
        print("Không có ảnh nào để duyệt.")
        return 0
    order = sorted((pid for pid in products if only is None or pid in only), key=lambda pid: (len(pid), pid))
    if not order:
        raise ValueError("--only không khớp sản phẩm nào trong danh sách")
    # drag: điểm bắt đầu khi vẽ khung mới; edit: (handle, x0, y0, khung gốc) khi dời/đổi cỡ khung đang chọn
    state = {"index": next((i for i, im in enumerate(images) if not im.get("reviewed")), 0), "selected": None, "photo": None, "scale": 1.0, "drag": None, "edit": None, "shown": []}

    root = tk.Tk()
    root.title("Gán nhãn benchmark")
    root.geometry("1280x800")
    root.minsize(960, 600)
    canvas = tk.Canvas(root, bg="#222", highlightthickness=0, cursor="tcross")
    canvas.pack(side="left", fill="both", expand=True)
    side = tk.Frame(root, width=360, padx=10, pady=10)
    side.pack(side="right", fill="y")
    side.pack_propagate(False)

    title = tk.Label(side, anchor="w", font=("Segoe UI", 11, "bold"))
    title.pack(fill="x")
    info = tk.Label(side, anchor="w", fg="#666", wraplength=330, justify="left")
    info.pack(fill="x", pady=(0, 6))
    chosen = tk.Label(side, anchor="w", wraplength=330, justify="left", font=("Segoe UI", 10, "bold"))
    chosen.pack(fill="x", pady=(0, 6))
    tk.Label(side, text="Khung của ảnh này (✔ = bạn đã sửa):", anchor="w").pack(fill="x")
    box_list = tk.Listbox(side, font=("Segoe UI", 10), height=9, activestyle="none", exportselection=False)
    box_list.pack(fill="x", pady=(2, 6))
    tk.Label(side, text="Tìm sản phẩm (mã hoặc tên), Enter để gán:", anchor="w").pack(fill="x")
    search = tk.Entry(side, font=("Segoe UI", 11))
    search.pack(fill="x", pady=(2, 4))
    listbox = tk.Listbox(side, font=("Segoe UI", 10), activestyle="none", exportselection=False)
    listbox.pack(fill="both", expand=True, pady=(0, 6))
    status = tk.Label(side, anchor="w", fg="#0a6", wraplength=330, justify="left")
    status.pack(fill="x", pady=(0, 6))
    approve_button = tk.Button(side, text="Ảnh này đúng hết → ảnh sau (A)")
    approve_button.pack(fill="x", pady=1)
    delete_button = tk.Button(side, text="Xoá khung đang chọn (Delete)")
    delete_button.pack(fill="x", pady=1)
    nav = tk.Frame(side)
    nav.pack(fill="x", pady=1)
    prev_button = tk.Button(nav, text="← Ảnh trước")
    prev_button.pack(side="left", fill="x", expand=True)
    next_button = tk.Button(nav, text="Ảnh sau →")
    next_button.pack(side="left", fill="x", expand=True)

    def current() -> dict:
        return images[state["index"]]

    def name_of(pid: str | None) -> str:
        return "(chưa có sản phẩm)" if not pid else f"{pid} · {products.get(pid, 'không có trong catalog')}"

    def persist(message: str = "", error: bool = False) -> None:
        save_labels(directory, labels)
        status.configure(text=message, fg="#c00" if error else "#0a6")

    def fill_products() -> None:
        needle = search.get().strip().lower()
        shown = [pid for pid in order if not needle or needle in pid.lower() or needle in str(products[pid]).lower()]
        state["shown"] = shown
        listbox.delete(0, "end")
        for pid in shown:
            listbox.insert("end", name_of(pid))
        if shown:
            listbox.selection_set(0)

    def draw() -> None:
        image = current()
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 200)
        scale = min((width - 8) / image["width"], (height - 8) / image["height"])
        state["scale"] = scale
        canvas.delete("all")
        try:
            with Image.open(directory / "images" / image["file_name"]) as opened:
                picture = ImageOps.exif_transpose(opened).convert("RGB").resize((max(1, int(image["width"] * scale)), max(1, int(image["height"] * scale))))
            state["photo"] = ImageTk.PhotoImage(picture)  # giữ tham chiếu, nếu không Tk bỏ ảnh
            canvas.create_image(0, 0, anchor="nw", image=state["photo"])
        except (OSError, ValueError) as exc:
            state["photo"] = None
            canvas.create_text(20, 20, anchor="nw", fill="white", text=f"Không mở được ảnh: {exc}")
        for i, box in enumerate(image["boxes"]):
            x1, y1, x2, y2 = (v * scale for v in box["bbox"])
            picked = i == state["selected"]
            color = COLORS[box_kind(box, only)]
            canvas.create_rectangle(x1, y1, x2, y2, outline="#ffffff" if picked else color, width=4 if picked else 2)
            tag = canvas.create_text(x1 + 3, y1 + 2, anchor="nw", fill="white", font=("Segoe UI", 9, "bold"), text=(box.get("product_id") or "?") + (" ✔" if was_edited(box) else ""))
            canvas.tag_lower(canvas.create_rectangle(canvas.bbox(tag), fill=color, outline=""), tag)
        done = sum(1 for im in images if im.get("reviewed"))
        title.configure(text=f"Ảnh {state['index'] + 1}/{len(images)} · đã duyệt {done}" + ("  ✔" if image.get("reviewed") else ""))
        info.configure(text=f"{image['file_name']} — {len(image['boxes'])} khung, {unlabeled(image)} chưa có sản phẩm")
        box_list.delete(0, "end")
        for i, box in enumerate(image["boxes"]):
            box_list.insert("end", f"{'✔' if was_edited(box) else '    '} {i + 1}. {name_of(box.get('product_id'))}")
            box_list.itemconfigure(i, foreground=COLORS[box_kind(box, only)])
        if state["selected"] is not None:
            box_list.selection_set(state["selected"])
            box_list.see(state["selected"])
        picked_box = image["boxes"][state["selected"]] if state["selected"] is not None else None
        chosen.configure(text="Khung đang chọn: " + name_of(picked_box.get("product_id")) if picked_box else "Chưa chọn khung nào")

    def touch() -> None:
        image = current()
        image["touched"] = True
        image["reviewed"] = False  # sửa xong phải duyệt lại

    def go(step: int) -> None:
        state["index"] = (state["index"] + step) % len(images)
        state["selected"] = None
        status.configure(text="")
        draw()

    def approve(_event: object = None) -> None:
        try:
            mark_reviewed(current())
        except ValueError as exc:
            persist(str(exc), error=True)
            return
        persist("Đã duyệt")
        if all(im.get("reviewed") for im in images):
            draw()
            messagebox.showinfo("Xong", f"Đã duyệt hết {len(images)} ảnh.\nNhãn: {directory / COCO_NAME}")
            return
        go(1)

    def unapprove(_event: object = None) -> None:
        current()["reviewed"] = False
        persist("Đã bỏ duyệt ảnh này")
        draw()

    def assign(_event: object = None) -> None:
        picked = listbox.curselection()
        if state["selected"] is None:
            status.configure(text="Chọn một khung trước (chạm vào khung trên ảnh)", fg="#c00")
            return
        if not picked:
            return
        box = current()["boxes"][state["selected"]]
        box["product_id"], box["source"], box["edited"] = state["shown"][picked[0]], "manual", True
        touch()
        persist(f"Đã gán: {name_of(box['product_id'])}")
        search.delete(0, "end")
        fill_products()
        root.focus_set()
        draw()

    def delete(_event: object = None) -> None:
        if state["selected"] is None:
            return
        current()["boxes"].pop(state["selected"])
        state["selected"] = None
        touch()
        persist("Đã xoá khung")
        draw()

    def at(event: object) -> tuple[float, float]:
        return getattr(event, "x") / state["scale"], getattr(event, "y") / state["scale"]

    def edge_under(x: float, y: float) -> str | None:
        """Viền/góc của khung ĐANG CHỌN dưới con trỏ (không tính phần bên trong)."""
        if state["selected"] is None:
            return None
        handle = grab_handle(current()["boxes"][state["selected"]]["bbox"], x, y, GRAB_PX / state["scale"])
        return None if handle == "move" else handle

    def rubber(bbox: list[float]) -> None:
        canvas.delete("rubber")
        canvas.create_rectangle(*(v * state["scale"] for v in bbox), outline="#38bdf8", dash=(4, 3), width=2, tags="rubber")

    def press(event: object) -> None:
        x, y = at(event)
        boxes = current()["boxes"]
        state["drag"] = state["edit"] = None
        edge = edge_under(x, y)
        if edge:
            state["edit"] = (edge, x, y, list(boxes[state["selected"]]["bbox"]))
            return
        hits = [i for i, box in enumerate(boxes) if box["bbox"][0] <= x <= box["bbox"][2] and box["bbox"][1] <= y <= box["bbox"][3]]
        if hits:
            # khung nhỏ nhất chứa điểm bấm: chọn được vật nằm trong khung lớn hơn
            state["selected"] = min(hits, key=lambda i: (boxes[i]["bbox"][2] - boxes[i]["bbox"][0]) * (boxes[i]["bbox"][3] - boxes[i]["bbox"][1]))
            state["edit"] = ("move", x, y, list(boxes[state["selected"]]["bbox"]))
            root.focus_set()
            draw()
        else:
            state["drag"] = (x, y)

    def edited(event: object) -> list[float] | None:
        """Khung đang dời/đổi cỡ tại vị trí con trỏ; None khi mới nhích chuột (coi như bấm chọn)."""
        handle, x0, y0, origin = state["edit"]
        x, y = at(event)
        if max(abs(x - x0), abs(y - y0)) * state["scale"] < MOVE_PX:
            return None
        image = current()
        return drag_box(origin, handle, x - x0, y - y0, image["width"], image["height"])

    def hover(event: object) -> None:
        x, y = at(event)
        canvas.configure(cursor=CURSORS.get(edge_under(x, y) or "", "tcross"))

    def motion(event: object) -> None:
        if state["edit"] is not None:
            bbox = edited(event)
            if bbox:
                canvas.configure(cursor=CURSORS[state["edit"][0]])
                rubber(bbox)
            return
        if state["drag"] is None:
            return
        x, y = at(event)
        rubber([*state["drag"], x, y])

    def release(event: object) -> None:
        if state["edit"] is not None:
            bbox = edited(event)
            state["edit"] = None
            if bbox:
                current()["boxes"][state["selected"]].update(bbox=bbox, edited=True)
                touch()
                persist("Đã sửa khung")
                draw()
            hover(event)
            return
        if state["drag"] is None:
            return
        x, y = at(event)
        x0, y0 = state["drag"]
        state["drag"] = None
        image = current()
        bbox = clamp_box([x0, y0, x, y], image["width"], image["height"])
        if bbox is None:
            state["selected"] = None  # bấm vào vùng trống: bỏ chọn
        else:
            image["boxes"].append({"bbox": bbox, "product_id": None, "source": "manual", "confidence": None})
            state["selected"] = len(image["boxes"]) - 1
            touch()
            persist("Khung mới: chọn sản phẩm cho nó")
            search.focus_set()
        draw()

    def pick_row(_event: object = None) -> None:
        picked = box_list.curselection()
        if picked:
            state["selected"] = picked[0]
            root.focus_set()
            draw()

    def typing() -> bool:
        return root.focus_get() is search

    def unless_typing(action):  # phím tắt chữ không được ăn mất ký tự đang gõ vào ô tìm
        return lambda event: None if typing() else action(event)

    def close(_event: object = None) -> None:
        save_labels(directory, labels)
        root.destroy()

    search.bind("<KeyRelease>", lambda event: fill_products() if getattr(event, "keysym", "") not in ("Return", "Up", "Down") else None)
    search.bind("<Return>", assign)
    listbox.bind("<Double-Button-1>", assign)
    listbox.bind("<Return>", assign)
    box_list.bind("<<ListboxSelect>>", pick_row)
    approve_button.configure(command=approve)
    delete_button.configure(command=delete)
    prev_button.configure(command=lambda: go(-1))
    next_button.configure(command=lambda: go(1))
    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<Motion>", hover)
    canvas.bind("<B1-Motion>", motion)
    canvas.bind("<ButtonRelease-1>", release)
    canvas.bind("<Configure>", lambda _event: draw())
    root.bind("<a>", unless_typing(approve))
    root.bind("<u>", unless_typing(unapprove))
    root.bind("<Delete>", unless_typing(delete))
    root.bind("<Left>", unless_typing(lambda _event: go(-1)))
    root.bind("<Right>", unless_typing(lambda _event: go(1)))
    root.bind("<Escape>", close)
    root.protocol("WM_DELETE_WINDOW", close)

    fill_products()
    draw()
    root.mainloop()
    done = sum(1 for im in images if im.get("reviewed"))
    print(f"Đã duyệt {done}/{len(images)} ảnh. Nhãn COCO: {directory / COCO_NAME}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Gán nhãn ảnh benchmark: pipeline đề xuất, người duyệt.")
    commands = parser.add_subparsers(dest="command", required=True)
    step1 = commands.add_parser("propose", help="chạy pipeline trên từng ảnh để đề xuất khung + sản phẩm (GPU)")
    step1.add_argument("--images", required=True, type=Path, help="thư mục ảnh chưa gán nhãn")
    step1.add_argument("--out", required=True, type=Path, help="thư mục benchmark mới (tạo nếu chưa có)")
    step1.add_argument("--config", type=Path, help="file config của pipeline (mặc định configs/config.yaml)")
    step2 = commands.add_parser("review", help="mở cửa sổ duyệt và sửa nhãn")
    step2.add_argument("--dir", required=True, type=Path, help="thư mục benchmark mới (đã chạy propose)")
    step2.add_argument("--only", help="chỉ liệt kê các sản phẩm này để chọn, ví dụ 21-39 hoặc 21,24,29-32")
    args = parser.parse_args(argv)
    try:
        if args.command == "propose":
            if not args.images.is_dir():
                raise ValueError(f"Không thấy thư mục ảnh: {args.images}")
            return propose(args.images, args.out, args.config)
        return review(args.dir, parse_only(args.only) if args.only else None)
    except ValueError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
