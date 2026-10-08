"""Sinh bộ DỮ LIỆU DEMO TỔNG HỢP cho Stocktaking AI (ảnh thật do script vẽ ra).

Sinh ẢNH + nhãn + metadata để chạy pipeline thật bằng backend mock trên CPU.

Chỉ cần numpy + opencv (không cần torch/faiss/pydantic). Xác định (seed cố định).

    python scripts/make_demo_dataset.py            # ghi vào data_demo/
    python scripts/make_demo_dataset.py --out X    # thư mục khác
    python scripts/make_demo_dataset.py --check    # chỉ kiểm tra tính nhất quán

Đầu ra (mặc định `data_demo/`, không đụng `data/` thật):
    gallery/<folder>/*.jpg     50 SKU x 6 ảnh (4 mặt + 2 biến thể sáng)
    benchmark/                 8 SKU lõi, 31 cảnh (giống benchmark thật) + COCO
    benchmark_full/            đủ 50 SKU, 60 cảnh + COCO (phủ cả SKU ngoài lõi)
    golden/                    15 cảnh + expected.json (so hồi quy sau mỗi sprint)
    query/                     6 cảnh không nhãn (+ query_truth.json)
    metadata/, seed/           products.json, product_ids.json, product_colors.json
    seed/product_prices.json   giá ngẫu nhiên (VND); 8 SKU cố ý null để thử luồng nhập giá tay
    seed/expected_evidence.json  bằng chứng tường minh mong đợi cho từng SKU
    configs/config.demo.yaml   (ghi vào <repo>/configs/) backend mock, CPU, trỏ data_demo/

DỮ LIỆU GIẢ LẬP: chỉ để kiểm tra pipeline chạy ổn định, KHÔNG phải số liệu thật.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SEED = 20260929

# ---- Khoảng trống ID giống catalog thật (không lấp): 22 SKU thật có khoảng trống 10,11,14,16,19,20
ID_GAPS = {10, 11, 14, 16, 19, 20}
N_SKU = 50
SKU_IDS = [i for i in range(1, 100) if i not in ID_GAPS][:N_SKU]  # 1..56 bỏ 6 khoảng trống

CORE_COUNTS = {1: 20, 2: 9, 3: 18, 4: 20, 5: 16, 6: 17, 7: 85, 8: 88}  # như benchmark thật (273)
BARCODE_IDS = [1, 3, 4, 5, 6, 9, 12, 13, 21, 25, 33, 44]  # 12/50 có barcode (thật: 5/22)
COLOR_REFS = {  # ColorReference có sẵn (BR641, OR210 cố ý THIẾU, như thật)
    "BE203": (199, 161, 148), "PK300": (109, 63, 62),
    "RD410": (176, 48, 60), "RD420": (196, 90, 96),
}
# SKU 1-8 giữ đúng vai trò như dữ liệu thật
CORE_SKUS = {
    1: ("Xit Chong Nang UV Sunny", "SUNNY", "", "box"),
    2: ("But Chi Chan May (BR641)", "BR641", "BR641", "box"),
    3: ("Phan Ma Hong (OR210)", "OR210", "OR210", "box"),
    4: ("Bong Phan Puff", "PUFF", "", "box"),
    5: ("Phan Mat Palette (BE203)", "BE203", "BE203", "box"),
    6: ("Phan Mat Palette (PK300)", "PK300", "PK300", "box"),
    7: ("Hop Ngoai ABA", "ABA", "", "box"),
    8: ("Hop Ngoai ABC", "ABC", "", "box"),
}
CATS = [("Kem Tay", "KT", "tube"), ("Kem Duong", "KD", "jar"), ("Sua Rua Mat", "SR", "tube"),
        ("Dau Goi", "DG", "bottle"), ("Xit Khoang", "XK", "bottle"), ("Tay Trang", "TT", "bottle"),
        ("Son Duong", "SD", "tube"), ("Mat Na", "MN", "pouch"), ("Sua Tam", "ST", "bottle")]
SCENTS = ["Lavender", "Cam", "Tra Xanh", "Hoa Hong", "Bac Ha", "Dao", "Dua", "Yen Mach", "Oai Huong",
          "Chanh", "Nho", "Sen", "Cafe", "Bo", "Duong Chat"]
PAIR_WEIGHT = (30, 31)  # cặp khác nhau chỉ ở khối lượng tịnh
LIP_COLOR_IDS = {40: "RD410", 41: "RD420"}
CONFUSABLE = [(7, 8), (30, 31)]
NO_PRICE_IDS = {8, 13, 31, 37, 44, 47, 52, 55}  # cố ý thiếu giá: có cả SKU barcode (13, 44) và thuộc cặp dễ nhầm (8, 31)
FORCE = {5: ["barcode", "color"], 6: ["barcode", "color"], 7: ["ocr"], 8: ["ocr"], 30: ["ocr"], 31: ["ocr"]}

MIN_GAP = 16  # px giữa hai hộp không chồng: dưới ngưỡng này viền bị dilate của detector nối thành một
SCENE_W, SCENE_H = 1280, 960
# Detector mock chấm conf = 0.5*độ_chữ_nhật + 0.5*min(tỉ_lệ_diện_tích/0.05, 1), ngưỡng 0.70 => hộp phải chiếm
# >= ~2.8% diện tích ảnh. Chọn kích thước theo DIỆN TÍCH (không theo chiều cao: chai/tuýp thon sẽ quá nhỏ).
AREA_RATIO_RANGE = (0.034, 0.048)
VIEWS = ("front", "back", "left", "right")


# --------------------------------------------------------------------------- catalog
def ean13(seed_digits: str) -> str:
    d = [int(c) for c in seed_digits]
    s = sum(x * (3 if i % 2 else 1) for i, x in enumerate(d))
    return seed_digits + str((10 - s % 10) % 10)


def build_skus() -> list[dict]:
    rng = np.random.default_rng(SEED)
    skus: list[dict] = []
    gen_i = 0
    for n, pid in enumerate(SKU_IDS):
        if pid in CORE_SKUS:
            name, front, color_code, shape = CORE_SKUS[pid]
            weight = ""
            folder = f"{pid:02d}_" + re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
        else:
            cat, ccode, shape = CATS[gen_i % len(CATS)]
            scent = SCENTS[(gen_i // len(CATS) + gen_i) % len(SCENTS)]
            weight = ""
            color_code = LIP_COLOR_IDS.get(pid, "")
            if pid in PAIR_WEIGHT:
                cat, ccode, shape, scent = "Sua Tam", "ST", "bottle", "Mix"
                weight = "100G" if pid == PAIR_WEIGHT[0] else "200G"
                name = f"Sua Tam Mix {weight.lower()}"
                front = "MIX120"
            elif color_code:
                cat, ccode, shape = "Son Tint", "SD", "tube"
                name, front = f"Son Tint ({color_code})", color_code
            else:
                name, front = f"{cat} {scent}", f"{ccode}{pid:02d}"
            # SKU không có mã trong tên (giống 9/22 thật) -> thư mục kiểu tên thô
            folder = (f"{pid:02d}_" + re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_"))
            gen_i += 1
        if pid in BARCODE_IDS and shape in ("bottle", "tube"):
            shape = "box"
        barcode = ean13(f"893{100000000 + pid * 137:09d}") if pid in BARCODE_IDS else ""
        hue = int((pid * 37) % 180)
        if pid in (7, 8):  # cặp gần như giống hệt: chỉ khác chữ
            hue = 100
        if pid in PAIR_WEIGHT:
            hue = 20
        if pid in (5, 6):
            hue = 150
        pattern = int(rng.integers(0, 5))
        if pid in (7, 8, *PAIR_WEIGHT, 5, 6):
            pattern = 3
        skus.append(dict(pid=pid, name=name, folder=folder, front=front, weight=weight,
                         color_code=color_code, shape=shape, barcode=barcode, hue=hue,
                         sat=int(rng.integers(110, 225)), val=int(rng.integers(150, 235)),
                         pattern=pattern))
    return skus


def hsv(h, s, v):
    return tuple(int(x) for x in cv2.cvtColor(np.uint8([[[h % 180, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0])


# --------------------------------------------------------------------------- vẽ sản phẩm
SHAPES = {"box": (300, 420), "bottle": (150, 430), "jar": (270, 270), "tube": (130, 410), "pouch": (280, 380)}


def _shape_mask(shape: str, w: int, h: int) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    r = {"box": 12, "bottle": 24, "jar": 34, "tube": 20, "pouch": 26}[shape]
    def rrect(x1, y1, x2, y2, rad):
        cv2.rectangle(m, (x1 + rad, y1), (x2 - rad, y2), 255, -1)
        cv2.rectangle(m, (x1, y1 + rad), (x2, y2 - rad), 255, -1)
        for cx, cy in ((x1 + rad, y1 + rad), (x2 - rad, y1 + rad), (x1 + rad, y2 - rad), (x2 - rad, y2 - rad)):
            cv2.circle(m, (cx, cy), rad, 255, -1)
    if shape == "bottle":
        rrect(0, int(h * 0.16), w - 1, h - 1, r)
        rrect(int(w * 0.30), 0, int(w * 0.70), int(h * 0.20), 8)
    else:
        rrect(0, 0, w - 1, h - 1, r)
    return m


def _put_fit(img, text, box, color, thick=2, font=cv2.FONT_HERSHEY_DUPLEX, rot=False):
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    if not text or bw < 8 or bh < 8:
        return
    scale = 3.0
    while scale > 0.25:
        (tw, th), base = cv2.getTextSize(text, font, scale, thick)
        if tw <= bw * 0.92 and th + base <= bh * 0.92:
            break
        scale -= 0.1
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    tile = np.zeros((bh, bw, 3), np.uint8)
    tm = np.zeros((bh, bw), np.uint8)
    org = ((bw - tw) // 2, (bh + th) // 2)
    cv2.putText(tile, text, org, font, scale, color, thick, cv2.LINE_AA)
    cv2.putText(tm, text, org, font, scale, 255, thick, cv2.LINE_AA)
    region = img[y1:y2, x1:x2]
    region[tm > 0] = tile[tm > 0]


def _ean_bars(code: str) -> list[int]:
    L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
    G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
    R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
    par = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]
    d = [int(c) for c in code]
    bits = "101" + "".join((L if p == "L" else G)[x] for p, x in zip(par[d[0]], d[1:7])) + "01010" \
           + "".join(R[x] for x in d[7:13]) + "101"
    return [int(b) for b in bits]


def draw_sprite(sku: dict, view: str) -> tuple[np.ndarray, np.ndarray]:
    """Trả (bgr, mask) của một mặt sản phẩm."""
    W, H = SHAPES[sku["shape"]]
    if view in ("left", "right"):
        W = max(50, int(W * 0.45))
    mask = _shape_mask(sku["shape"], W, H)
    c1 = hsv(sku["hue"], sku["sat"], sku["val"])
    c2 = hsv(sku["hue"] + 28, min(255, sku["sat"] + 20), max(90, sku["val"] - 50))
    c3 = hsv(sku["hue"] - 20, 60, 245)
    img = np.full((H, W, 3), c1, np.uint8)
    p = sku["pattern"]
    if p == 1:
        for x in range(-H, W, 46):
            cv2.line(img, (x, H), (x + H, 0), c2, 16)
    elif p == 2:
        for y in range(20, H, 52):
            for x in range(20 + (y // 52 % 2) * 26, W, 52):
                cv2.circle(img, (x, y), 9, c2, -1)
    elif p == 3:
        img[:, W // 2:] = c2
    elif p == 4:
        g = np.linspace(0, 1, H)[:, None, None]
        img = (np.array(c1)[None, None] * (1 - g) + np.array(c2)[None, None] * g).astype(np.uint8)
        img = np.repeat(img, W, axis=1)
    img[: int(H * 0.12)] = c2 if p != 3 else img[: int(H * 0.12)]
    if sku["shape"] == "bottle":
        img[: int(H * 0.20)] = tuple(int(x * 0.55) for x in c2)
    if sku["shape"] == "tube":
        img[int(H * 0.93):] = tuple(int(x * 0.5) for x in c2)
    dark = (35, 35, 35)
    if view == "front":
        lx1, lx2 = int(W * 0.08), int(W * 0.92)
        ly1, ly2 = int(H * 0.30), int(H * 0.68)
        cv2.rectangle(img, (lx1, ly1), (lx2, ly2), c3, -1)
        _put_fit(img, sku["front"], (lx1, ly1 + 4, lx2, ly1 + int((ly2 - ly1) * (0.62 if sku["weight"] else 0.85))), dark, 3)
        if sku["weight"]:
            _put_fit(img, sku["weight"], (lx1, ly1 + int((ly2 - ly1) * 0.62), lx2, ly2 - 2), dark, 2)
        if sku["color_code"] in COLOR_REFS or sku["color_code"] in ("BR641", "OR210"):
            ref = COLOR_REFS.get(sku["color_code"], (120, 80, 60))
            pan = (int(W * 0.15), int(H * 0.72), int(W * 0.85), int(H * 0.90))
            cv2.rectangle(img, pan[:2], pan[2:], ref[::-1], -1)
            cv2.rectangle(img, pan[:2], pan[2:], (60, 60, 60), 2)
    elif view == "back":
        lx1, lx2 = int(W * 0.08), int(W * 0.92)
        cv2.rectangle(img, (lx1, int(H * 0.16)), (lx2, int(H * 0.94)), (245, 245, 245), -1)
        for i in range(5):
            y = int(H * (0.20 + i * 0.05))
            cv2.line(img, (lx1 + 10, y), (lx2 - 10 - (i % 3) * 25, y), (150, 150, 150), 3)
        _put_fit(img, sku["front"], (lx1, int(H * 0.46), lx2, int(H * 0.54)), (90, 90, 90), 1)
        if sku["barcode"]:
            bits = _ean_bars(sku["barcode"])
            mod = max(1, min(3, (lx2 - lx1 - 20) // len(bits)))
            bw = mod * len(bits)
            bx = (W - bw) // 2
            by1, by2 = int(H * 0.60), int(H * 0.86)
            cv2.rectangle(img, (bx - 8, by1 - 6), (bx + bw + 8, by2 + 6), (255, 255, 255), -1)
            for i, b in enumerate(bits):
                if b:
                    cv2.rectangle(img, (bx + i * mod, by1), (bx + (i + 1) * mod - 1, by2), (0, 0, 0), -1)
    else:  # mặt bên: dải chữ dọc
        tile = np.zeros((W, H, 3), np.uint8)
        tile[:] = c3
        _put_fit(tile, sku["front"], (10, 4, H - 10, W - 4), dark, 2)
        rot = cv2.rotate(tile, cv2.ROTATE_90_CLOCKWISE if view == "left" else cv2.ROTATE_90_COUNTERCLOCKWISE)
        y1, y2 = int(H * 0.25), int(H * 0.75)
        band = cv2.resize(rot, (W, y2 - y1))
        img[y1:y2] = band
    # Viền tối: bao bì thật có cạnh rõ; đảm bảo Canny bắt được cạnh kể cả khi màu gần màu nền.
    edge = cv2.subtract(mask, cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=3))
    img[edge > 0] = (25, 25, 25)
    img[mask == 0] = 0
    return img, mask


# --------------------------------------------------------------------------- gallery
def counter_background(rng, h, w):
    """Nền bàn xám-nâu, KHÔNG có đường/vân sắc nét.

    Lý do: detector mock dùng Canny + dilate + contour ngoài cùng; một đường ngang chạy suốt ảnh sẽ nối
    viền các sản phẩm thành một đường bao khổng lồ và làm recall detector tụt còn ~0.27 (đã đo). Nền chỉ
    có gradient nhẹ + nhiễu, giống mặt bàn phẳng.
    """
    base = np.array([112, 128, 142], np.float32)
    yy = np.linspace(0.92, 1.08, h)[:, None, None]
    bg = base[None, None] * yy + rng.normal(0, 4.0, (h, w, 3))
    return np.clip(bg, 0, 255).astype(np.uint8)


def render_gallery(out: Path, skus: list[dict]) -> None:
    rng = np.random.default_rng(SEED + 1)
    for sku in skus:
        d = out / sku["folder"]
        d.mkdir(parents=True, exist_ok=True)
        plan = [("front", 1.0), ("back", 1.0), ("left", 1.0), ("right", 1.0), ("front", 0.85), ("front", 1.12)]
        for i, (view, gain) in enumerate(plan):
            spr, m = draw_sprite(sku, view)
            mg = 8
            h, w = spr.shape[:2]
            bg = counter_background(rng, h + 2 * mg, w + 2 * mg)
            a = np.zeros((h + 2 * mg, w + 2 * mg), np.float32)
            a[mg:mg + h, mg:mg + w] = cv2.GaussianBlur(m, (3, 3), 0) / 255.0
            canvas = np.zeros_like(bg)
            canvas[mg:mg + h, mg:mg + w] = spr
            comp = bg * (1 - a[..., None]) + canvas * a[..., None]
            comp = np.clip(comp * gain + rng.normal(0, 2.0, comp.shape), 0, 255).astype(np.uint8)
            cv2.imwrite(str(d / f"{view}_{i}.jpg"), comp, [cv2.IMWRITE_JPEG_QUALITY, 92])


# --------------------------------------------------------------------------- cảnh
def make_scene(rng, skus_by_id, pids, want_overlap=0, view_p=(0.7, 0.18, 0.06, 0.06)):
    bg = counter_background(rng, SCENE_H, SCENE_W).astype(np.float32)
    idmap = np.zeros((SCENE_H, SCENE_W), np.int32)
    placed = []  # (pid, bbox_full, view)
    items = []
    overlaps_left = want_overlap
    for k, pid in enumerate(pids, start=1):
        sku = skus_by_id[pid]
        view = str(rng.choice(VIEWS, p=view_p))
        spr, m = draw_sprite(sku, view)
        target_area = float(rng.uniform(*AREA_RATIO_RANGE)) * SCENE_W * SCENE_H
        s = min(np.sqrt(target_area / (spr.shape[0] * spr.shape[1])), 0.45 * SCENE_H / spr.shape[0])
        spr = cv2.resize(spr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        m = cv2.resize(m, (spr.shape[1], spr.shape[0]), interpolation=cv2.INTER_NEAREST)
        ang = float(rng.normal(0, 7)) if rng.random() > 0.10 else float(rng.choice([90, -90]) + rng.normal(0, 8))
        h, w = spr.shape[:2]
        diag = int(np.hypot(h, w)) + 4
        M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        M[0, 2] += (diag - w) / 2
        M[1, 2] += (diag - h) / 2
        spr = cv2.warpAffine(spr, M, (diag, diag), flags=cv2.INTER_LINEAR)
        m = cv2.warpAffine(m, M, (diag, diag), flags=cv2.INTER_NEAREST)
        ys, xs = np.nonzero(m)
        if len(ys) == 0:
            continue
        bx1, by1, bx2, by2 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        spr, m = spr[by1:by2, bx1:bx2], m[by1:by2, bx1:bx2]
        # Viền vẽ SAU khi resize+xoay để độ dày cố định (viền vẽ trước bị co/mờ -> Canny thấy viền hở ->
        # contour mở, độ chữ nhật thấp, detector mock loại vật). Đã đo: đây là nguyên nhân chính bỏ sót.
        m_bin = (m > 127).astype(np.uint8) * 255
        edge = cv2.subtract(m_bin, cv2.erode(m_bin, np.ones((3, 3), np.uint8), iterations=3))
        spr[edge > 0] = (25, 25, 25)
        h, w = m.shape
        if h >= SCENE_H - 10 or w >= SCENE_W - 10:
            continue
        need_ov = overlaps_left > 0 and len(placed) > 0
        ok = False
        for _ in range(400):
            x = int(rng.integers(5, SCENE_W - w - 5))
            y = int(rng.integers(5, SCENE_H - h - 5))
            worst = 0.0
            best_pair = 0.0
            for _, (px1, py1, px2, py2), _v in placed:
                iw = min(x + w, px2) - max(x, px1)
                ih = min(y + h, py2) - max(y, py1)
                if iw > 0 and ih > 0:
                    inter = iw * ih
                    ratio = inter / min(w * h, (px2 - px1) * (py2 - py1))
                    worst = max(worst, ratio)
                    best_pair = max(best_pair, ratio)
                elif iw > -MIN_GAP and ih > -MIN_GAP:  # không chồng nhưng quá sát: coi như vi phạm
                    worst = max(worst, 1.0)
            if need_ov:
                if 0.12 <= best_pair <= 0.30:
                    ok = True
                    overlaps_left -= 1
                    break
            elif worst < 0.02:
                ok = True
                break
        if not ok:
            continue
        region = (slice(y, y + h), slice(x, x + w))
        a = cv2.GaussianBlur(m, (3, 3), 0).astype(np.float32) / 255.0
        sh = cv2.GaussianBlur(m, (0, 0), 6).astype(np.float32) / 255.0
        sy, sx = min(6, SCENE_H - y - h), min(6, SCENE_W - x - w)
        if sy > 0 and sx > 0:
            bg[y + sy:y + h + sy, x + sx:x + w + sx] *= (1 - 0.30 * sh[:h, :w])[..., None][: h, : w]
        bg[region] = bg[region] * (1 - a[..., None]) + spr.astype(np.float32) * a[..., None]
        idmap[region][m > 127] = k
        placed.append((pid, (x, y, x + w, y + h), view))
        items.append((k, pid, view))
    img = bg * float(rng.uniform(0.82, 1.12))
    img = cv2.GaussianBlur(img, (0, 0), float(rng.uniform(0.3, 1.1)))
    img = np.clip(img + rng.normal(0, 2.5, img.shape), 0, 255).astype(np.uint8)
    anns = []
    for k, pid, view in items:
        ys, xs = np.nonzero(idmap == k)
        if len(ys) < 500:
            continue
        anns.append(dict(pid=pid, bbox=[int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)], view=view))
    return img, anns


def split_sizes(rng, total, n, lo, hi):
    sizes = rng.integers(lo, hi + 1, n)
    while sizes.sum() != total:
        i = int(rng.integers(0, n))
        if sizes.sum() < total and sizes[i] < hi:
            sizes[i] += 1
        elif sizes.sum() > total and sizes[i] > lo:
            sizes[i] -= 1
    return sizes.tolist()


def write_coco(out: Path, skus_by_id, scenes, cat_ids):
    (out / "images").mkdir(parents=True, exist_ok=True)
    images, anns = [], []
    aid = 1
    for i, (img, ann) in enumerate(scenes, start=1):
        fn = f"scene_{i:03d}.jpg"
        cv2.imwrite(str(out / "images" / fn), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        images.append(dict(id=i, file_name=fn, width=SCENE_W, height=SCENE_H))
        for a in ann:
            anns.append(dict(id=aid, image_id=i, category_id=a["pid"], bbox=a["bbox"],
                             area=a["bbox"][2] * a["bbox"][3], iscrowd=0))
            aid += 1
    cats = [dict(id=p, name=skus_by_id[p]["folder"], supercategory="product") for p in sorted(cat_ids)]
    (out / "_annotations.coco.json").write_text(
        json.dumps(dict(images=images, annotations=anns, categories=cats), ensure_ascii=False, indent=1), encoding="utf-8")
    return images, anns


def pids_for(rng, pool, per_sku, n_scenes, lo, hi):
    bag = [p for p in pool for _ in range(per_sku)]
    rng.shuffle(bag)
    sizes = split_sizes(rng, len(bag), n_scenes, lo, hi)
    out, c = [], 0
    for s in sizes:
        out.append(bag[c:c + s])
        c += s
    return out


def render_sets(out: Path, skus: list[dict]) -> dict:
    by_id = {s["pid"]: s for s in skus}
    rng = np.random.default_rng(SEED + 2)
    stats = {}
    # benchmark lõi: đúng phân bố như benchmark thật
    bag = [p for p, n in CORE_COUNTS.items() for _ in range(n)]
    rng.shuffle(bag)
    sizes = split_sizes(rng, len(bag), 31, 6, 10)
    scenes, c = [], 0
    for s in sizes:
        scenes.append(make_scene(rng, by_id, bag[c:c + s], want_overlap=int(rng.integers(0, 2))))
        c += s
    im, an = write_coco(out / "benchmark", by_id, scenes, list(CORE_COUNTS))
    stats["benchmark"] = (len(im), len(an))
    # benchmark đủ 50 SKU
    plan = pids_for(rng, [s["pid"] for s in skus], 10, 60, 6, 10)
    scenes = [make_scene(rng, by_id, p, want_overlap=int(rng.integers(0, 2))) for p in plan]
    im, an = write_coco(out / "benchmark_full", by_id, scenes, [s["pid"] for s in skus])
    stats["benchmark_full"] = (len(im), len(an))
    # golden: 15 cảnh, có nhãn expected.json
    g_rng = np.random.default_rng(SEED + 3)
    plan = pids_for(g_rng, [s["pid"] for s in skus], 2, 15, 5, 9)
    (out / "golden").mkdir(parents=True, exist_ok=True)
    (out / "golden" / "images").mkdir(exist_ok=True)
    exp = {}
    for i, p in enumerate(plan, start=1):
        ov = 2 if i % 4 == 0 else 0
        vp = (0.4, 0.3, 0.15, 0.15) if i % 5 == 0 else (0.7, 0.18, 0.06, 0.06)
        img, ann = make_scene(g_rng, by_id, p, want_overlap=ov, view_p=vp)
        fn = f"golden_{i:02d}.jpg"
        cv2.imwrite(str(out / "golden" / "images" / fn), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        counts: dict[str, int] = {}
        for a in ann:
            counts[str(a["pid"])] = counts.get(str(a["pid"]), 0) + 1
        exp[fn] = dict(counts=counts, n_objects=len(ann), overlap_requested=ov,
                       non_front_views=sum(1 for a in ann if a["view"] != "front"),
                       boxes=[dict(product_id=str(a["pid"]), bbox=a["bbox"]) for a in ann])
    (out / "golden" / "expected.json").write_text(json.dumps(exp, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["golden"] = (len(exp), sum(v["n_objects"] for v in exp.values()))
    # query: không nhãn
    q_rng = np.random.default_rng(SEED + 4)
    (out / "query").mkdir(parents=True, exist_ok=True)
    truth = {}
    plan = pids_for(q_rng, [s["pid"] for s in skus], 1, 6, 6, 10)
    for i, p in enumerate(plan, start=1):
        img, ann = make_scene(q_rng, by_id, p)
        fn = f"query_{i:02d}.jpg"
        cv2.imwrite(str(out / "query" / fn), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        truth[fn] = [str(a["pid"]) for a in ann]
    (out / "query" / "query_truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
    stats["query"] = (len(truth), sum(len(v) for v in truth.values()))
    return stats


# --------------------------------------------------------------------------- metadata + config
def write_metadata(out: Path, skus: list[dict]) -> None:
    products = [dict(product_id=str(s["pid"]), product_name=s["name"], brand="", category="",
                     barcode=s["barcode"], description="", folder=s["folder"], image_count=6) for s in skus]
    ids = dict(next_id=max(SKU_IDS) + 1, products={str(s["pid"]): s["folder"] for s in skus})
    colors = {c: dict(name=c, rgb=list(rgb), hex="#%02X%02X%02X" % rgb) for c, rgb in COLOR_REFS.items()}
    evidence = {}
    for s in skus:
        # Cặp chỉ khác khối lượng: chữ mặt trước ("MIX120") in giống nhau ở cả hai SKU nên KHÔNG
        # được làm từ khoá (luật: cặp dễ nhầm bắt buộc OCR không được trùng token).
        kw = [s["weight"]] if s["weight"] else [s["front"]]
        evidence[str(s["pid"])] = dict(
            ocr_keywords=kw, color_code=s["color_code"] or None, barcode=s["barcode"] or None,
            force_evidence=FORCE.get(s["pid"], []),
            confusable_with=[str(b if a == s["pid"] else a) for a, b in CONFUSABLE if s["pid"] in (a, b)])
    for sub in ("metadata", "seed"):
        d = out / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "products.json").write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (d / "product_ids.json").write_text(json.dumps(ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (d / "product_colors.json").write_text(json.dumps(colors, indent=2) + "\n", encoding="utf-8")
    p_rng = np.random.default_rng(SEED + 5)
    prices = {str(s["pid"]): (None if s["pid"] in NO_PRICE_IDS else int(p_rng.integers(16, 501)) * 500)
              for s in skus}  # VND, bội số 500, 8.000-250.000
    (out / "seed" / "product_prices.json").write_text(json.dumps(prices, indent=2) + "\n", encoding="utf-8")
    (out / "seed" / "expected_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_demo_config(out_rel: str, skus: list[dict], path: Path | None = None) -> Path:
    """Sinh config demo từ configs/config.yaml (schema sau Phase 1B: catalog trong DB).

    Catalog demo nằm ở <out_rel>/db/app.db, nạp bằng:
        python -m engine.catalog.migrate --seed-dir <out_rel>/seed --legacy-config configs/config.demo.yaml --db <out_rel>/db/app.db
    (force_evidence/confusable_with lấy từ seed/expected_evidence.json.)

    Lưu ý: configs/config.demo.yaml đang commit được sinh ngày 2026-10-03 và là mốc của cổng G-demo
    (compare_validate --exact). Chạy lại sẽ kéo theo mọi thay đổi của config.yaml từ đó (ngưỡng detect,
    lọc khung, xoay gallery, ...) nên phải đo lại mốc data/baseline/demo.
    """
    base = ROOT / "configs" / "config.yaml"
    t = base.read_text(encoding="utf-8").replace("\r\n", "\n")

    def sub(pattern, repl, count=None, flags=0):
        nonlocal t
        t, n = re.subn(pattern, repl, t, flags=flags)
        if count is not None and n != count:
            raise SystemExit(f"config drift: {pattern!r} khớp {n} lần, mong đợi {count}")

    sub(r'device: "cuda"', 'device: "cpu"', 5)
    sub(r'(detection:\n  backend: )"rf_detr"', r'\1"mock_contour"', 1)
    sub(r'(refinement:\n  enabled: true\n  backend: )"sam2"', r'\1"none"', 1)
    sub(r'(retrieval:\n  backend: )"siglip2"', r'\1"mock_visual_embedding"', 1)
    sub(r'"data/(gallery|benchmark|benchmark/images|benchmark/_annotations\.coco\.json|query|outputs|cache|metadata)"',
        rf'"{out_rel}/\1"', 8)
    sub(r'"data/cache/(gallery_index\.faiss|gallery_metadata\.json|color_signatures\.npz)"', rf'"{out_rel}/cache/\1"')
    sub(r'"data/cache/logs"', f'"{out_rel}/cache/logs"', 1)
    sub(r'(  db_path: )"data/db/app\.db"', rf'\1"{out_rel}/db/app.db"', 1)
    sub(r'(  ocr:\n    enabled: )true', r'\1false  # demo: bật lại (true) để thử EasyOCR', 1)
    # Bộ demo có bảng màu tham chiếu (seed/product_colors.json) nhưng chưa có chữ ký màu từ gallery.
    sub(r'(    mode: )"signature"', r'\1"roi"  # demo: chưa build chữ ký màu cho gallery demo', 1)
    # Config thật có index FAISS build sẵn (cờ = false); bộ demo chưa có index nên phải bật để `python -m engine` tự build.
    sub(r'(  build_gallery_index: )false', r'\1true  # demo: build index từ data_demo/gallery ở lần chạy đầu', 1)
    header = ("# CONFIG DEMO - sinh bởi scripts/make_demo_dataset.py (đừng sửa tay, chạy lại script).\n"
              "# Backend mock, CPU, dữ liệu trong data_demo/. Chạy:\n"
              "#   python -m engine --mode validate --config configs/config.demo.yaml "
              "--benchmark-dir data_demo/benchmark\n")
    path = path or ROOT / "configs" / "config.demo.yaml"
    path.write_text(header + t, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- kiểm tra
def check(out: Path) -> list[str]:
    errs: list[str] = []
    ids = json.loads((out / "metadata" / "product_ids.json").read_text(encoding="utf-8"))["products"]
    products = json.loads((out / "metadata" / "products.json").read_text(encoding="utf-8"))
    folders = {p.name for p in (out / "gallery").iterdir() if p.is_dir()}
    if len(products) != N_SKU or len(ids) != N_SKU:
        errs.append(f"số SKU {len(products)}/{len(ids)} != {N_SKU}")
    if folders != set(ids.values()):
        errs.append("thư mục gallery != product_ids.json")
    if {int(k) for k in ids} & ID_GAPS:
        errs.append("ID khoảng trống bị dùng")
    prices = json.loads((out / "seed" / "product_prices.json").read_text(encoding="utf-8"))
    if set(prices) != set(ids):
        errs.append("product_prices.json không khớp danh sách SKU")
    if {k for k, v in prices.items() if v is None} != {str(i) for i in NO_PRICE_IDS}:
        errs.append("tập SKU thiếu giá lệch NO_PRICE_IDS")
    if any(v is not None and (v <= 0 or v % 500) for v in prices.values()):
        errs.append("giá không hợp lệ")
    for f in folders:
        if len(list((out / "gallery" / f).glob("*.jpg"))) != 6:
            errs.append(f"{f}: không đủ 6 ảnh")
    for name in ("benchmark", "benchmark_full"):
        coco = json.loads((out / name / "_annotations.coco.json").read_text(encoding="utf-8"))
        imgs = {i["id"]: i for i in coco["images"]}
        for a in coco["annotations"]:
            x, y, w, h = a["bbox"]
            im = imgs[a["image_id"]]
            if not (0 <= x and 0 <= y and x + w <= im["width"] and y + h <= im["height"] and w > 0 and h > 0):
                errs.append(f"{name}: bbox ngoài ảnh {a['id']}")
            if str(a["category_id"]) not in ids:
                errs.append(f"{name}: category_id {a['category_id']} không có trong catalog")
        if any(not (out / name / "images" / i["file_name"]).is_file() for i in coco["images"]):
            errs.append(f"{name}: thiếu file ảnh")
    return errs


def sanity(out: Path) -> None:
    """Tái hiện đường detector mock + retrieval mock để biết ĐỘ KHÓ của bộ demo (không phải metric thật).

    Dùng tham số mặc định của config demo (Canny 40/120, blur 5, conf>=0.70, diện tích 0.1%-60%, tỉ lệ 0.15-6,
    crop đệm 4px rồi resize 1024x1024). Nếu bạn đổi các tham số đó, số này chỉ mang tính tham khảo.
    """
    ids = json.loads((out / "metadata" / "product_ids.json").read_text(encoding="utf-8"))["products"]

    def detect(im):
        h, w = im.shape[:2]
        g = cv2.GaussianBlur(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        e = cv2.dilate(cv2.Canny(g, 40, 120), np.ones((3, 3), np.uint8), iterations=2)
        cs, _ = cv2.findContours(e, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out_ = []
        for c in cs:
            x, y, bw, bh = cv2.boundingRect(c)
            if bw * bh <= 0:
                continue
            ar, asp = bw * bh / (h * w), bw / bh
            if not (0.001 <= ar <= 0.60 and 0.15 <= asp <= 6.0):
                continue
            conf = min(1.0, 0.5 * min(1.0, cv2.contourArea(c) / (bw * bh)) + 0.5 * min(ar / 0.05, 1.0))
            if conf >= 0.70:
                out_.append((x, y, x + bw, y + bh))
        return out_

    def iou(a, b):
        iw, ih = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
        if iw <= 0 or ih <= 0:
            return 0.0
        i = iw * ih
        return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)

    def emb(im):
        hsv_ = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv_], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256]).flatten()
        g = cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), (16, 16), interpolation=cv2.INTER_AREA).flatten().astype(np.float32)
        v = np.concatenate([h, g]).astype(np.float32)[:768]
        return v / (np.linalg.norm(v) + 1e-9)

    gal, lab = [], []
    for pid, folder in ids.items():
        for p in (out / "gallery" / folder).glob("*.jpg"):
            gal.append(emb(cv2.imread(str(p))))
            lab.append(pid)
    gal = np.stack(gal)
    for name in ("benchmark", "benchmark_full"):
        coco = json.loads((out / name / "_annotations.coco.json").read_text(encoding="utf-8"))
        gts: dict[int, list] = {}
        for a in coco["annotations"]:
            x, y, w, h = a["bbox"]
            gts.setdefault(a["image_id"], []).append((x, y, x + w, y + h, str(a["category_id"])))
        tp = fp = fn = t1 = t5 = 0
        for img in coco["images"]:
            im = cv2.imread(str(out / name / "images" / img["file_name"]))
            H, W = im.shape[:2]
            G = gts.get(img["id"], [])
            used: set[int] = set()
            for d in detect(im):
                best, bj = 0.0, None
                for j, g in enumerate(G):
                    if j not in used and iou(d, g) > best:
                        best, bj = iou(d, g), j
                if best < 0.30:
                    fp += 1
                    continue
                used.add(bj)
                tp += 1
                x1, y1, x2, y2 = d
                crop = im[max(0, y1 - 4):min(H, y2 + 4), max(0, x1 - 4):min(W, x2 + 4)]
                sims = gal @ emb(cv2.resize(crop, (1024, 1024), interpolation=cv2.INTER_AREA))
                seen: list[str] = []
                for j2 in np.argsort(-sims):
                    if lab[j2] not in seen:
                        seen.append(lab[j2])
                    if len(seen) == 5:
                        break
                t1 += seen[0] == G[bj][4]
                t5 += G[bj][4] in seen
            fn += len(G) - len(used)
        print(f"[sanity:{name}] detector P={tp / max(tp + fp, 1):.2f} R={tp / max(tp + fn, 1):.2f} | "
              f"retrieval top1/top5 trên hộp khớp={t1 / max(tp, 1):.2f}/{t5 / max(tp, 1):.2f} "
              f"(chỉ báo độ khó; OCR tắt nên cặp 7/8, 30/31 không phân biệt được bằng retrieval)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data_demo")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    # Windows: stdout bị chuyển hướng dùng cp1252 -> in tiếng Việt sẽ lỗi.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    out = (ROOT / a.out).resolve()
    if a.check:
        errs = check(out)
        print("\n".join(errs) if errs else "OK: dữ liệu demo nhất quán")
        return 1 if errs else 0
    skus = build_skus()
    out.mkdir(parents=True, exist_ok=True)
    render_gallery(out / "gallery", skus)
    stats = render_sets(out, skus)
    write_metadata(out, skus)
    cfg = write_demo_config(a.out, skus)
    for k, (i, o) in stats.items():
        print(f"{k:15s} {i:3d} ảnh, {o:4d} đối tượng")
    print(f"gallery: {len(skus)} SKU x 6 ảnh | config: {cfg.relative_to(ROOT)}")
    errs = check(out)
    print("\n".join(errs) if errs else "check: OK")
    sanity(out)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
