"""Migrate catalog cũ (JSON + khoá trong config) vào DB SQLite. Idempotent, chỉ CHÈN, không ghi đè.

Nguồn (D11 — thẩm quyền ID khi lệch: ``id_mapping`` > ``products.json`` > ``product_ids.json``):
- ``<seed-dir>/products.json``, ``product_ids.json``, ``product_colors.json``
  (dữ liệu thật: ``data/metadata``; demo: ``data_demo/seed``);
- config cũ: ``catalog.id_mapping``, ``plugins.force_rules``, ``rerank.confusable_pairs``,
  ``plugins.ocr.min_text_length``;
- ``expected_evidence.json`` (nếu có, bộ demo) — nguồn ``ocr_keywords``/``color_code``;
  nếu không có, tái hiện ĐÚNG logic suy diễn từ tên của ``Reranker`` cũ (loại token chung);
- bảng ``product_overrides`` của web (barcode admin đã sửa) nếu DB đã có — gộp vào ``Product.barcode``.

Tên thư mục được so khớp sau chuẩn hoá NFKC (``id_mapping`` cũ viết dấu cách thường trong khi
thư mục thật dùng dấu cách toàn góc U+3000); ``gallery_folder`` luôn lưu đúng tên thư mục thật.

Dừng (lỗi) khi: trùng ID/thư mục, ``products.json`` và ``product_ids.json`` xung đột ngoài
``id_mapping``, barcode trùng, bằng chứng vi phạm luật, ``category_id`` benchmark thiếu trong catalog.

CLI:
    python -m engine.catalog.migrate --seed-dir data/metadata --legacy-config configs/config.yaml \
        --db data/db/app.db --gallery-dir data/gallery --benchmark-labels data/benchmark/_annotations.coco.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engine.catalog.validation import (
    FORCE_EVIDENCE_PLUGINS,
    normalize_ocr_keywords,
    rgb_to_hex,
    validate_catalog,
)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EXPECTED_MAX_ID_REAL = 28


class MigrationError(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("Migrate dừng vì lỗi:\n- " + "\n- ".join(self.errors))


def norm_folder(name: str) -> str:
    """Khoá so khớp tên thư mục: NFKC + gộp khoảng trắng."""
    return " ".join(unicodedata.normalize("NFKC", str(name)).split())


def _id_key(pid: str) -> tuple[int, str]:
    return (len(pid), pid)


# --------------------------------------------------------------------------------------
# Logic suy diễn CŨ của Reranker (sao chép nguyên văn để seed đúng kết quả đang chạy).
# Chỉ dùng lúc migrate dữ liệu thật; sau C8, Reranker không còn suy diễn từ tên.
# --------------------------------------------------------------------------------------

def legacy_catalog_tokens(product_name: str, min_text_length: int) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", product_name):
        token = token.upper().strip()
        if token and len(token) >= min_text_length:
            tokens.append(token)
    return list(dict.fromkeys(tokens))


def legacy_color_code(product_name: str, known_codes: dict[str, Any] | None = None) -> str | None:
    if not product_name:
        return None
    text = str(product_name).upper().strip()
    bracket_groups = re.findall(r"[\(\（\[\【]([A-Z0-9_-]{2,20})[\)\）\]\】]", text)
    for value in reversed(bracket_groups):
        compact = re.sub(r"[^A-Z0-9]", "", value)
        if re.fullmatch(r"[A-Z]{2,6}\d{2,6}", compact):
            return compact
    if known_codes:
        for value in reversed(bracket_groups):
            compact = re.sub(r"[^A-Z0-9]", "", value)
            if compact in known_codes:
                return compact
    matches = re.findall(r"\b[A-Z]{2,6}\d{2,6}\b", text)
    if matches:
        if known_codes:
            for match in reversed(matches):
                if match in known_codes:
                    return match
        return matches[-1]
    if known_codes:
        for match in reversed(re.findall(r"\b[A-Z]{3,6}\b", text)):
            if match in known_codes:
                return match
    return None


# --------------------------------------------------------------------------------------
# Nguồn
# --------------------------------------------------------------------------------------

@dataclass
class LegacySources:
    products: list[dict[str, Any]]
    product_ids: dict[str, str]
    product_ids_next: int | None
    colors: dict[str, dict[str, Any]]
    id_mapping: dict[str, str] = field(default_factory=dict)
    force_rules: dict[str, list[str]] = field(default_factory=dict)
    confusable_pairs: list[list[str]] = field(default_factory=list)
    ocr_min_length: int = 3
    expected_evidence: dict[str, dict[str, Any]] | None = None
    gallery_folders: dict[str, int] | None = None  # tên thư mục thật -> số ảnh
    benchmark_category_ids: list[int] | None = None
    barcode_overrides: dict[str, str] = field(default_factory=dict)
    expected_max_id: int | None = None
    manual_evidence: dict[str, dict[str, Any]] | None = None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_sources(
    seed_dir: Path,
    legacy_config: Path,
    gallery_dir: Path | None = None,
    benchmark_labels: Path | None = None,
    db_path: Path | None = None,
    expected_evidence: Path | None = None,
    manual_evidence: Path | None = None,
) -> LegacySources:
    seed_dir = Path(seed_dir)
    products_raw = _read_json(seed_dir / "products.json")
    products = products_raw if isinstance(products_raw, list) else products_raw.get("products", [])
    ids_raw = _read_json(seed_dir / "product_ids.json")
    colors_path = seed_dir / "product_colors.json"
    colors = _read_json(colors_path) if colors_path.is_file() else {}

    cfg = yaml.safe_load(Path(legacy_config).read_text(encoding="utf-8")) or {}
    catalog_cfg = cfg.get("catalog") or {}
    plugins_cfg = cfg.get("plugins") or {}
    rerank_cfg = cfg.get("rerank") or {}

    if expected_evidence is None and (seed_dir / "expected_evidence.json").is_file():
        expected_evidence = seed_dir / "expected_evidence.json"

    gallery = None
    if gallery_dir is not None:
        gallery = {
            d.name: sum(1 for f in d.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)
            for d in sorted(Path(gallery_dir).iterdir())
            if d.is_dir()
        }

    categories = None
    if benchmark_labels is not None:
        coco = _read_json(Path(benchmark_labels))
        categories = sorted({int(a["category_id"]) for a in coco.get("annotations", [])})

    overrides: dict[str, str] = {}
    if db_path is not None and Path(db_path).is_file():
        con = sqlite3.connect(db_path)
        try:
            has = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_overrides'"
            ).fetchone()
            if has:
                overrides = {
                    str(pid): str(bc) for pid, bc in con.execute("SELECT product_id, barcode FROM product_overrides")
                }
        finally:
            con.close()

    return LegacySources(
        products=products,
        product_ids={str(k): str(v) for k, v in (ids_raw.get("products") or {}).items()},
        product_ids_next=ids_raw.get("next_id"),
        colors=colors,
        id_mapping={str(k): str(v) for k, v in (catalog_cfg.get("id_mapping") or {}).items()},
        force_rules={str(k): list(v) for k, v in (plugins_cfg.get("force_rules") or {}).items()},
        confusable_pairs=[[str(x) for x in pair] for pair in (rerank_cfg.get("confusable_pairs") or [])],
        ocr_min_length=int((plugins_cfg.get("ocr") or {}).get("min_text_length", 3)),
        expected_evidence=_read_json(expected_evidence) if expected_evidence else None,
        gallery_folders=gallery,
        benchmark_category_ids=categories,
        barcode_overrides=overrides,
        manual_evidence=_read_json(manual_evidence) if manual_evidence else None,
    )


# --------------------------------------------------------------------------------------
# Kế hoạch (hàm thuần)
# --------------------------------------------------------------------------------------

@dataclass
class PlannedProduct:
    product_id: str
    product_name: str
    gallery_folder: str | None
    barcode: str | None
    brand: str | None
    category: str | None
    description: str | None
    image_count: int
    needs_naming: bool


@dataclass
class MigrationPlan:
    products: list[PlannedProduct]
    evidence: dict[str, dict[str, Any]]
    colors: dict[str, tuple[int, int, int, str]]  # code -> (r, g, b, hex)
    next_product_id: int
    warnings: list[str]
    info: list[str]


def _blank(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def build_plan(src: LegacySources) -> MigrationPlan:
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    # ---- 1. ID <-> thư mục, theo thẩm quyền ----
    pj_by_id: dict[str, dict[str, Any]] = {}
    for item in src.products:
        pid = str(item.get("product_id", "")).strip()
        if not pid.isdigit():
            errors.append(f"products.json: product_id không hợp lệ {pid!r}")
            continue
        if pid in pj_by_id:
            errors.append(f"products.json: trùng product_id {pid}")
            continue
        pj_by_id[pid] = item

    real_folders = src.gallery_folders or {}
    real_by_norm: dict[str, str] = {}
    for name in real_folders:
        key = norm_folder(name)
        if key in real_by_norm:
            errors.append(f"Hai thư mục gallery trùng nhau sau chuẩn hoá: {real_by_norm[key]!r} / {name!r}")
        real_by_norm[key] = name

    # Ứng viên thư mục theo từng nguồn (đã chuẩn hoá).
    folder_pj = {pid: norm_folder(it.get("folder") or "") for pid, it in pj_by_id.items() if it.get("folder")}
    folder_ids = {pid: norm_folder(f) for pid, f in src.product_ids.items()}
    folder_map = {pid: norm_folder(f) for pid, f in src.id_mapping.items()}

    known_folder_keys = set(real_by_norm) | set(folder_pj.values()) | set(folder_ids.values())
    resolved: dict[str, str] = {}  # pid -> folder (chuẩn hoá)

    for pid in sorted(set(folder_pj) | set(folder_ids), key=_id_key):
        a, b = folder_pj.get(pid), folder_ids.get(pid)
        if a and b and a != b and pid not in folder_map:
            errors.append(f"SKU {pid}: products.json ({a!r}) và product_ids.json ({b!r}) xung đột thư mục")
            continue
        resolved[pid] = a or b

    for pid, folder in sorted(folder_map.items(), key=lambda kv: _id_key(kv[0])):
        if not pid.isdigit():
            errors.append(f"id_mapping: product_id không hợp lệ {pid!r}")
            continue
        if folder not in known_folder_keys:
            info.append(f"ID {pid} chỉ có trong id_mapping (thư mục {folder!r} không tồn tại) -> khoảng trống, không tạo SKU")
            continue
        if pid in resolved and resolved[pid] != folder:
            warnings.append(f"SKU {pid}: id_mapping ({folder!r}) khác nguồn JSON ({resolved[pid]!r}) -> theo id_mapping")
        resolved[pid] = folder

    by_folder: dict[str, list[str]] = {}
    for pid, folder in resolved.items():
        by_folder.setdefault(folder, []).append(pid)
    for folder, pids in by_folder.items():
        if len(pids) > 1:
            errors.append(f"Thư mục {folder!r} được gán cho nhiều ID: {sorted(pids, key=_id_key)}")

    if src.gallery_folders is not None:
        for key, real in real_by_norm.items():
            if key not in by_folder:
                warnings.append(f"Thư mục gallery {real!r} chưa có ID trong nguồn cũ -> dùng sync_gallery để thêm SKU mới")
        for pid, folder in resolved.items():
            if folder not in real_by_norm:
                warnings.append(f"SKU {pid}: thư mục {folder!r} không có trong gallery")

    # ---- 2. Product ----
    planned: list[PlannedProduct] = []
    for pid in sorted(set(resolved) | set(pj_by_id), key=_id_key):
        item = pj_by_id.get(pid, {})
        folder_key = resolved.get(pid)
        folder_real = real_by_norm.get(folder_key) if folder_key else None
        if folder_real is None and folder_key:
            folder_real = item.get("folder") or src.product_ids.get(pid) or folder_key
        name = _blank(item.get("product_name"))
        needs_naming = name is None or (folder_real is not None and norm_folder(name) == norm_folder(folder_real))
        if name is None:
            name = folder_real or f"SKU {pid}"
        barcode = _blank(src.barcode_overrides.get(pid)) or _blank(item.get("barcode"))
        if pid in src.barcode_overrides and _blank(src.barcode_overrides[pid]) != _blank(item.get("barcode")):
            info.append(f"SKU {pid}: barcode lấy từ product_overrides của web ({barcode!r})")
        image_count = (
            real_folders.get(folder_real, 0) if src.gallery_folders is not None and folder_real in real_folders
            else int(item.get("image_count") or 0)
        )
        planned.append(
            PlannedProduct(
                product_id=pid,
                product_name=name,
                gallery_folder=folder_real,
                barcode=barcode,
                brand=_blank(item.get("brand")),
                category=_blank(item.get("category")),
                description=_blank(item.get("description")),
                image_count=image_count,
                needs_naming=needs_naming,
            )
        )

    barcodes = Counter(p.barcode for p in planned if p.barcode)
    for bc, n in barcodes.items():
        if n > 1:
            errors.append(f"Barcode {bc} bị dùng cho {n} SKU")

    ids = {p.product_id for p in planned}

    # ---- 3. Màu tham chiếu ----
    colors: dict[str, tuple[int, int, int, str]] = {}
    for code, entry in sorted(src.colors.items()):
        rgb = entry.get("rgb") if isinstance(entry, dict) else entry
        try:
            r, g, b = (int(v) for v in rgb)
            hex_value = rgb_to_hex(r, g, b)
        except (TypeError, ValueError) as exc:
            errors.append(f"Màu {code}: RGB không hợp lệ ({exc})")
            continue
        declared = entry.get("hex") if isinstance(entry, dict) else None
        if declared and str(declared).upper() != hex_value:
            warnings.append(f"Màu {code}: hex khai báo {declared} khác RGB {hex_value} -> theo RGB")
        colors[code] = (r, g, b, hex_value)

    # ---- 4. Bằng chứng ----
    evidence: dict[str, dict[str, Any]] = {pid: {} for pid in sorted(ids, key=_id_key)}

    for pid, plugins in src.force_rules.items():
        bad = set(plugins) - FORCE_EVIDENCE_PLUGINS
        if bad:
            errors.append(f"force_rules SKU {pid}: plugin lạ {sorted(bad)}")
        evidence.setdefault(pid, {})["force_evidence"] = sorted(set(plugins))

    for pair in src.confusable_pairs:
        if len(pair) != 2 or pair[0] == pair[1]:
            errors.append(f"confusable_pairs không hợp lệ: {pair}")
            continue
        a, b = pair
        for x, y in ((a, b), (b, a)):
            lst = evidence.setdefault(x, {}).setdefault("confusable_with", [])
            if y not in lst:
                lst.append(y)
    for types in evidence.values():
        if "confusable_with" in types:
            types["confusable_with"] = sorted(types["confusable_with"], key=_id_key)

    # Bộ demo sau C4: config không còn force_rules/confusable_pairs -> lấy từ expected_evidence.json.
    take_rules_from_expected = (
        src.expected_evidence is not None and not src.force_rules and not src.confusable_pairs
    )
    if take_rules_from_expected:
        for pid, exp in src.expected_evidence.items():
            pid = str(pid)
            if pid not in ids:
                continue
            if exp.get("force_evidence"):
                evidence[pid]["force_evidence"] = sorted(set(exp["force_evidence"]))
            if exp.get("confusable_with"):
                evidence[pid]["confusable_with"] = sorted(map(str, exp["confusable_with"]), key=_id_key)
        info.append("force_evidence/confusable_with lấy từ expected_evidence.json (config không còn khoá cũ)")

    if src.expected_evidence is not None:
        for pid, exp in src.expected_evidence.items():
            pid = str(pid)
            if pid not in ids:
                errors.append(f"expected_evidence: SKU {pid} không có trong catalog")
                continue
            kws = normalize_ocr_keywords(exp.get("ocr_keywords") or [], src.ocr_min_length)
            if kws:
                evidence[pid]["ocr_keywords"] = kws
            if exp.get("color_code"):
                evidence[pid]["color_code"] = str(exp["color_code"])
            # Đối chiếu chéo với config/products.json: phải khớp.
            if sorted(exp.get("force_evidence") or []) != evidence[pid].get("force_evidence", []):
                errors.append(f"SKU {pid}: force_evidence trong expected_evidence khác force_rules của config")
            if sorted(map(str, exp.get("confusable_with") or []), key=_id_key) != evidence[pid].get("confusable_with", []):
                errors.append(f"SKU {pid}: confusable_with trong expected_evidence khác confusable_pairs của config")
            prod = next(p for p in planned if p.product_id == pid)
            if _blank(exp.get("barcode")) != prod.barcode:
                errors.append(f"SKU {pid}: barcode expected_evidence {exp.get('barcode')!r} khác catalog {prod.barcode!r}")
        info.append("ocr_keywords/color_code seed từ expected_evidence.json")
    else:
        names = {p.product_id: pj_by_id.get(p.product_id, {}).get("product_name") or "" for p in planned}
        tokens = {pid: legacy_catalog_tokens(name, src.ocr_min_length) for pid, name in names.items()}
        counts = Counter(t for toks in tokens.values() for t in toks)
        shared = sorted(t for t, n in counts.items() if n > 1)
        if shared:
            info.append(f"Loại token OCR chung (xuất hiện ở >= 2 SKU): {shared}")
        for pid, toks in tokens.items():
            kept = [t for t in toks if counts[t] == 1]
            if kept:
                evidence[pid]["ocr_keywords"] = kept
            code = legacy_color_code(names[pid], src.colors)
            if code:
                evidence[pid]["color_code"] = code
        info.append("ocr_keywords/color_code seed từ logic suy diễn cũ của Reranker")

    # Bằng chứng khai báo tay (ví dụ chữ in trên bao bì) ghi đè kết quả suy diễn cho SKU được liệt kê.
    if src.manual_evidence:
        for pid, types in src.manual_evidence.items():
            pid = str(pid)
            if pid not in ids:
                errors.append(f"manual_evidence: SKU {pid} không có trong catalog")
                continue
            for etype, value in types.items():
                if etype == "ocr_keywords":
                    value = normalize_ocr_keywords(value, src.ocr_min_length)
                evidence[pid][etype] = value
            info.append(f"SKU {pid}: bằng chứng khai báo tay {sorted(types)}")

    evidence = {pid: types for pid, types in evidence.items() if types}

    report = validate_catalog(ids, evidence, colors.keys(), src.ocr_min_length)
    errors.extend(report.errors)
    warnings.extend(report.warnings)

    # ---- 5. Benchmark ----
    if src.benchmark_category_ids is not None:
        missing = [c for c in src.benchmark_category_ids if str(c) not in ids]
        if missing:
            errors.append(f"category_id của benchmark không có trong catalog: {missing}")

    # ---- 6. next_product_id (không bao giờ tái dùng ID) ----
    all_ids = [int(p) for p in set(pj_by_id) | set(src.product_ids) | set(folder_map) if p.isdigit()]
    next_id = max(all_ids, default=0) + 1
    if src.product_ids_next:
        next_id = max(next_id, int(src.product_ids_next))
    max_pj = max((int(p) for p in pj_by_id), default=0)
    if src.expected_max_id is not None and max_pj != src.expected_max_id:
        warnings.append(f"max(ID) của products.json = {max_pj}, kỳ vọng {src.expected_max_id}")

    if errors:
        raise MigrationError(errors)
    return MigrationPlan(planned, evidence, colors, next_id, warnings, info)


# --------------------------------------------------------------------------------------
# Ghi DB (chỉ chèn)
# --------------------------------------------------------------------------------------

@dataclass
class ApplyResult:
    inserted: Counter
    skipped: Counter
    differs: list[str]


def apply_plan(engine, plan: MigrationPlan, updated_by: str = "migrate") -> ApplyResult:
    from engine.catalog.db import (
        META_NEXT_PRODUCT_ID,
        META_SEED_COMPLETED_AT,
        ColorReference,
        Product,
        ProductEvidence,
        Session,
        create_all,
        get_meta,
        now_iso,
        select,
        set_meta,
    )

    create_all(engine)
    inserted: Counter = Counter()
    skipped: Counter = Counter()
    differs: list[str] = []
    with Session(engine) as s:
        for p in plan.products:
            row = s.get(Product, p.product_id)
            if row is None:
                s.add(Product(**p.__dict__))
                inserted["product"] += 1
            else:
                skipped["product"] += 1
                if row.gallery_folder != p.gallery_folder:
                    differs.append(f"SKU {p.product_id}: DB có thư mục {row.gallery_folder!r}, nguồn {p.gallery_folder!r} (giữ DB)")
        s.flush()

        existing = {
            (e.product_id, e.evidence_type) for e in s.exec(select(ProductEvidence)).all()
        }
        for pid, types in plan.evidence.items():
            for etype, value in types.items():
                if (pid, etype) in existing:
                    skipped["evidence"] += 1
                    continue
                s.add(
                    ProductEvidence(
                        product_id=pid,
                        evidence_type=etype,
                        value_json=json.dumps(value, ensure_ascii=False),
                        updated_by=updated_by,
                    )
                )
                inserted["evidence"] += 1

        for code, (r, g, b, hex_value) in plan.colors.items():
            if s.get(ColorReference, code) is None:
                s.add(ColorReference(color_code=code, r=r, g=g, b=b, hex=hex_value, source="seed"))
                inserted["color"] += 1
            else:
                skipped["color"] += 1

        current = get_meta(s, META_NEXT_PRODUCT_ID)
        new_next = max(int(current) if current else 0, plan.next_product_id)
        if current != str(new_next):
            set_meta(s, META_NEXT_PRODUCT_ID, str(new_next))
        if get_meta(s, META_SEED_COMPLETED_AT) is None:
            set_meta(s, META_SEED_COMPLETED_AT, now_iso())
        s.commit()
    return ApplyResult(inserted, skipped, differs)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Migrate catalog JSON/config cũ vào DB catalog (idempotent).")
    ap.add_argument("--seed-dir", required=True, type=Path)
    ap.add_argument("--legacy-config", required=True, type=Path)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--db", type=Path, help="file SQLite")
    target.add_argument(
        "--db-url", help="URL SQLAlchemy (vd postgresql+psycopg://...); bảng phải đã có (alembic upgrade head)"
    )
    ap.add_argument("--gallery-dir", type=Path)
    ap.add_argument("--benchmark-labels", type=Path)
    ap.add_argument("--expected-evidence", type=Path)
    ap.add_argument("--manual-evidence", type=Path, help="JSON {product_id: {evidence_type: value}} khai báo tay")
    ap.add_argument("--expected-max-id", type=int, help="Cảnh báo nếu max(ID) products.json khác giá trị này")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ in kế hoạch, không ghi DB")
    args = ap.parse_args(argv)

    src = load_sources(
        args.seed_dir, args.legacy_config, args.gallery_dir, args.benchmark_labels,
        db_path=args.db, expected_evidence=args.expected_evidence, manual_evidence=args.manual_evidence,
    )
    src.expected_max_id = args.expected_max_id
    try:
        plan = build_plan(src)
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for line in plan.info:
        print(f"[INFO] {line}")
    for line in plan.warnings:
        print(f"[CẢNH BÁO] {line}")
    n_ev = sum(len(t) for t in plan.evidence.values())
    print(f"Kế hoạch: {len(plan.products)} SKU, {n_ev} dòng bằng chứng, {len(plan.colors)} màu, next_product_id={plan.next_product_id}")
    if args.dry_run:
        for pid, types in plan.evidence.items():
            print(f"  {pid}: {json.dumps(types, ensure_ascii=False)}")
        return 0

    from engine.catalog.db import make_engine, make_engine_from_url

    engine = make_engine_from_url(args.db_url) if args.db_url else make_engine(args.db)
    try:
        res = apply_plan(engine, plan)
    finally:
        engine.dispose()
    print(f"Đã chèn: {dict(res.inserted)}; bỏ qua (đã có): {dict(res.skipped)}")
    for line in res.differs:
        print(f"[CẢNH BÁO] {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
