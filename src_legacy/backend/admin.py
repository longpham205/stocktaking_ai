"""Nghiệp vụ QUẢN TRỊ của web POS: sản phẩm, bằng chứng nhận diện, gallery, thiết lập nâng cao, báo cáo, nhân viên, nhật ký.

`AdminMixin` được trộn vào `service.App`; dùng chung DB, catalog, hàng đợi suy luận của App.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.catalog.validation import (FORCE_EVIDENCE_PLUGINS, normalize_ocr_keywords, normalize_ocr_token, parse_hex,
                                    rgb_to_hex, validate_catalog)
from src.core.config import build_config, read_raw_config

from .common import (CONFIRM_TEXT, EVIDENCE_FIELDS, _BARCODE_RE, _COLOR_CODE_RE, _USERNAME_RE, Session, _as_int, _err,
                     fold)
from .config_registry import BY_KEY, REGISTRY, get_path, normalize_value
from .db import utcnow
from .security import hash_password, sign_media, verify_password

log = logging.getLogger("backend")


class AdminMixin:
    # ------------------------------------------------------------------ quản trị: sản phẩm
    def admin_products(self, sess: Session, search: str = "", flt: str = "", page: int = 1, size: int = 50) -> dict:
        self._need_admin(sess)
        with self.db.read() as c:
            prices, barcodes = self._prices(c), self._barcodes(c)
        q = fold(search.strip())
        rows = []
        for pid, p in self.catalog.all().items():
            if q and q not in fold(p["name"]) and q not in pid and q not in barcodes.get(pid, ""):
                continue
            if flt == "missing_price" and pid in prices:
                continue
            if flt == "missing_barcode" and barcodes.get(pid):
                continue
            if flt == "needs_naming" and not p["needs_naming"]:
                continue
            rows.append(self._product_view(pid, prices, barcodes))
        page, size = max(1, page), max(1, min(200, size))
        return {"total": len(rows), "page": page, "size": size, "items": rows[(page - 1) * size: page * size],
                "missing_price": sum(1 for pid in self.catalog.all() if pid not in prices),
                "missing_barcode": sum(1 for pid in self.catalog.all() if not barcodes.get(pid)),
                "needs_naming": sum(1 for p in self.catalog.all().values() if p["needs_naming"])}

    def admin_update_product(self, sess: Session, pid: str, body: dict) -> dict:
        self._need_admin(sess)
        if not self.catalog.get(pid):
            raise _err(404, "NOT_FOUND", "Không thấy sản phẩm")
        if not ({"price", "barcode", "name"} & set(body)):
            raise _err(422, "VALIDATION_ERROR", "Chỉ sửa được 'price', 'barcode' và 'name'")
        catalog_changed = False
        with self.db.tx() as c:
            prices, barcodes = self._prices(c), self._barcodes(c)
            now = utcnow()

            def log_change(field: str, old, new) -> None:
                c.execute("INSERT INTO change_log(table_name,record_id,field_name,old_value,new_value,changed_by,changed_at) "
                          "VALUES('product',?,?,?,?,?,?)", (pid, field, None if old is None else str(old),
                                                            None if new is None else str(new), sess.user_id, now))
            if "price" in body:
                new = None if body["price"] is None else _as_int(body["price"], "price")
                if new != prices.get(pid):
                    log_change("price", prices.get(pid), new)
                    if new is None:
                        c.execute("DELETE FROM product_prices WHERE product_id=?", (pid,))
                    else:
                        c.execute("INSERT INTO product_prices VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET "
                                  "price=excluded.price, updated_at=excluded.updated_at", (pid, new, now))
                    prices = self._prices(c)
            if "barcode" in body:
                new = str(body["barcode"] or "").strip()
                if new and not _BARCODE_RE.match(new):
                    raise _err(422, "VALIDATION_ERROR", "Barcode gồm 4–32 ký tự chữ, số hoặc dấu gạch ngang")
                if new and any(b == new for k, b in barcodes.items() if k != pid):
                    raise _err(409, "BARCODE_DUPLICATE", "Barcode đã thuộc sản phẩm khác")
                if new != barcodes.get(pid, ""):
                    log_change("barcode", barcodes.get(pid, ""), new)
                    c.execute("UPDATE product SET barcode=?, updated_at=? WHERE product_id=?", (new or None, now, pid))
                    barcodes = self._barcodes(c)
                    catalog_changed = True
            if "name" in body:
                new = " ".join(str(body["name"] or "").split())
                if not 1 <= len(new) <= 120:
                    raise _err(422, "VALIDATION_ERROR", "Tên sản phẩm 1–120 ký tự")
                old = c.execute("SELECT product_name, needs_naming FROM product WHERE product_id=?", (pid,)).fetchone()
                if new != old["product_name"] or old["needs_naming"]:
                    log_change("name", old["product_name"], new)
                    c.execute("UPDATE product SET product_name=?, needs_naming=0, updated_at=? WHERE product_id=?", (new, now, pid))
                    catalog_changed = True
        if catalog_changed:
            self._after_catalog_change()
        return self._product_view(pid, prices, barcodes)

    # ------------------------------------------------------------------ quản trị: bằng chứng nhận diện + màu tham chiếu
    @staticmethod
    def _evidence_rows(c) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in c.execute("SELECT product_id, evidence_type, value_json FROM product_evidence"):
            out.setdefault(r["product_id"], {})[r["evidence_type"]] = json.loads(r["value_json"])
        return out

    @staticmethod
    def _require_confirm(body: dict) -> None:
        if body.get("confirm") is not True:
            raise _err(422, "CONFIRM_REQUIRED", f"Cần xác nhận: \"{CONFIRM_TEXT}\"", confirm_text=CONFIRM_TEXT)

    def _normalize_evidence(self, pid: str, field: str, value):
        """Chuẩn hoá giá trị gửi lên; rỗng -> None (xoá bằng chứng)."""
        if value is None or value == "" or value == []:
            return None
        if field == "color_code":
            code = str(value).strip().upper()
            if not _COLOR_CODE_RE.match(code):
                raise _err(422, "VALIDATION_ERROR", "Mã màu 2–20 ký tự chữ hoa, số, '_' hoặc '-'")
            return code
        if not isinstance(value, list) or not all(isinstance(v, (str, int)) for v in value):
            raise _err(422, "VALIDATION_ERROR", f"'{field}' phải là danh sách")
        if field == "ocr_keywords":
            try:
                return normalize_ocr_keywords([str(v) for v in value], self.s.ocr_min_length) or None
            except ValueError as exc:
                raise _err(422, "VALIDATION_ERROR", str(exc)) from exc
        if field == "force_evidence":
            bad = sorted({str(v) for v in value} - FORCE_EVIDENCE_PLUGINS)
            if bad:
                raise _err(422, "VALIDATION_ERROR", f"Plugin không hợp lệ: {bad} (chỉ ocr, color, barcode)")
            return sorted({str(v) for v in value})
        ids = sorted({str(v).strip() for v in value} - {""}, key=lambda x: (len(x), x))
        if pid in ids:
            raise _err(422, "VALIDATION_ERROR", "Không thể đặt sản phẩm dễ nhầm với chính nó")
        return ids or None

    def admin_product_evidence(self, sess: Session, pid: str) -> dict:
        self._need_admin(sess)
        if not self.catalog.get(pid):
            raise _err(404, "NOT_FOUND", "Không thấy sản phẩm")
        with self.db.read() as c:
            ev = self._evidence_rows(c).get(pid, {})
            prices, barcodes = self._prices(c), self._barcodes(c)
        return {"product": self._product_view(pid, prices, barcodes),
                "evidence": {f: ev.get(f, None if f == "color_code" else []) for f in EVIDENCE_FIELDS},
                "colors": self.admin_colors(sess), "confirm_text": CONFIRM_TEXT,
                "ocr_min_length": self.s.ocr_min_length, "gallery": self.gallery_urls(pid)}

    def admin_update_evidence(self, sess: Session, pid: str, body: dict) -> dict:
        """Sửa bằng chứng nhận diện của một SKU (một giao dịch, có change_log). ``confusable_with`` ghi hai chiều."""
        self._need_admin(sess)
        if not self.catalog.get(pid):
            raise _err(404, "NOT_FOUND", "Không thấy sản phẩm")
        self._require_confirm(body)
        fields = {f: self._normalize_evidence(pid, f, body[f]) for f in EVIDENCE_FIELDS if f in body}
        if not fields:
            raise _err(422, "VALIDATION_ERROR", f"Cần ít nhất một trường: {', '.join(EVIDENCE_FIELDS)}")
        with self.db.tx() as c:
            old = self._evidence_rows(c)
            new = json.loads(json.dumps(old))  # sao chép sâu: không được sửa lẫn vào `old` (dùng để so khác biệt)
            mine = new.setdefault(pid, {})
            for f, v in fields.items():
                if f == "confusable_with":
                    before, after = set(mine.get(f) or []), set(v or [])
                    for other in after - before:
                        lst = new.setdefault(other, {}).setdefault(f, [])
                        if pid not in lst:
                            lst.append(pid)
                            lst.sort(key=lambda x: (len(x), x))
                    for other in before - after:
                        lst = [x for x in new.get(other, {}).get(f, []) if x != pid]
                        if lst:
                            new[other][f] = lst
                        else:
                            new.get(other, {}).pop(f, None)
                if v is None:
                    mine.pop(f, None)
                else:
                    mine[f] = v
            ids = [r[0] for r in c.execute("SELECT product_id FROM product")]
            colors = [r[0] for r in c.execute("SELECT color_code FROM color_reference")]
            report = validate_catalog(ids, {k: v for k, v in new.items() if v}, colors, self.s.ocr_min_length)
            if report.errors:
                raise _err(422, "EVIDENCE_INVALID", "Bằng chứng không hợp lệ: " + "; ".join(report.errors),
                           errors=report.errors)
            now = utcnow()
            for rid in sorted(set(old) | set(new), key=lambda x: (len(x), x)):
                for f in sorted(set(old.get(rid, {})) | set(new.get(rid, {}))):
                    a, b = old.get(rid, {}).get(f), new.get(rid, {}).get(f)
                    if a == b:
                        continue
                    ja = None if a is None else json.dumps(a, ensure_ascii=False)
                    jb = None if b is None else json.dumps(b, ensure_ascii=False)
                    if b is None:
                        c.execute("DELETE FROM product_evidence WHERE product_id=? AND evidence_type=?", (rid, f))
                    else:
                        c.execute("INSERT INTO product_evidence(product_id,evidence_type,value_json,updated_by,updated_at) "
                                  "VALUES(?,?,?,?,?) ON CONFLICT(product_id,evidence_type) DO UPDATE SET "
                                  "value_json=excluded.value_json, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                                  (rid, f, jb, sess.username, now))
                    c.execute("INSERT INTO change_log(table_name,record_id,field_name,old_value,new_value,changed_by,changed_at) "
                              "VALUES('product_evidence',?,?,?,?,?,?)", (rid, f, ja, jb, sess.user_id, now))
        self._after_catalog_change()
        warnings = list(report.warnings)
        if any(not t.isascii() for t in (fields.get("ocr_keywords") or [])):
            warnings.append("Từ khoá có ký tự ngoài bảng chữ Latin — OCR hiện đọc tiếng Anh nên có thể không đọc ra")
        return {**self.admin_product_evidence(sess, pid), "warnings": warnings}

    def admin_colors(self, sess: Session) -> list[dict]:
        self._need_admin(sess)
        with self.db.read() as c:
            used: dict[str, list[str]] = {}
            for pid, ev in self._evidence_rows(c).items():
                if ev.get("color_code"):
                    used.setdefault(ev["color_code"], []).append(pid)
            rows = {r["color_code"]: r for r in c.execute("SELECT * FROM color_reference")}
        out = [{"code": k, "hex": r["hex"], "r": r["r"], "g": r["g"], "b": r["b"], "source": r["source"],
                "used_by": used.get(k, []), "missing": False} for k, r in sorted(rows.items())]
        out += [{"code": k, "hex": None, "used_by": v, "missing": True} for k, v in sorted(used.items()) if k not in rows]
        return out

    def admin_update_color(self, sess: Session, code: str, body: dict) -> dict:
        """Tạo/sửa (hex) hoặc xoá (hex = null) màu tham chiếu. Lưu RGB + hex; Reranker tự đổi sang Lab."""
        self._need_admin(sess)
        self._require_confirm(body)
        code = str(code).strip().upper()
        if not _COLOR_CODE_RE.match(code):
            raise _err(422, "VALIDATION_ERROR", "Mã màu 2–20 ký tự chữ hoa, số, '_' hoặc '-'")
        if "hex" not in body:
            raise _err(422, "VALIDATION_ERROR", "Cần trường 'hex' (#RRGGBB, hoặc null để xoá)")
        new_hex = None
        if body["hex"] is not None:
            try:
                new_hex = rgb_to_hex(*parse_hex(str(body["hex"])))
            except ValueError as exc:
                raise _err(422, "VALIDATION_ERROR", str(exc)) from exc
        with self.db.tx() as c:
            row = c.execute("SELECT hex FROM color_reference WHERE color_code=?", (code,)).fetchone()
            old_hex = row["hex"] if row else None
            if old_hex != new_hex:
                if new_hex is None:
                    c.execute("DELETE FROM color_reference WHERE color_code=?", (code,))
                else:
                    r, g, b = parse_hex(new_hex)
                    c.execute("INSERT INTO color_reference(color_code,r,g,b,hex,source,updated_at) VALUES(?,?,?,?,?,'manual',?) "
                              "ON CONFLICT(color_code) DO UPDATE SET r=excluded.r, g=excluded.g, b=excluded.b, hex=excluded.hex, "
                              "source='manual', updated_at=excluded.updated_at", (code, r, g, b, new_hex, utcnow()))
                c.execute("INSERT INTO change_log(table_name,record_id,field_name,old_value,new_value,changed_by,changed_at) "
                          "VALUES('color_reference',?,'hex',?,?,?,?)", (code, old_hex, new_hex, sess.user_id, utcnow()))
        if old_hex != new_hex:
            self._after_catalog_change()
        return next((x for x in self.admin_colors(sess) if x["code"] == code), {"code": code, "hex": None, "used_by": [], "missing": False})

    # ------------------------------------------------------------------ quản trị: ảnh gallery, thử bằng chứng, kiểm định
    GALLERY_MAX_SIDE = 1024
    _IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

    def _gallery_files(self, pid: str) -> list[Path]:
        """Ảnh gallery của SKU (thư mục lấy từ catalog, gốc gallery lấy từ config pipeline)."""
        folder = (self.catalog.repo.get_product(pid) or {}).get("gallery_folder")
        if not folder:
            return []
        try:
            rel = (read_raw_config(self.s.pipeline_config).get("paths") or {}).get("gallery_dir")
        except OSError:
            return []
        if not rel:
            return []
        base = Path(rel) if Path(rel).is_absolute() else Path(self.s.pipeline_config).resolve().parent.parent / rel
        d = base / folder
        return sorted(f for f in d.iterdir() if f.suffix.lower() in self._IMG_EXT) if d.is_dir() else []

    def gallery_urls(self, pid: str, limit: int = 12) -> list[str]:
        exp = int(time.time()) + self.s.media_url_ttl
        out = []
        for i, _ in enumerate(self._gallery_files(pid)[:limit]):
            rel = f"g/{pid}/{i}"
            out.append(f"/api/gallery/{pid}/{i}?exp={exp}&sig={sign_media(self.s.jwt_secret, rel, exp)}")
        return out

    def gallery_image(self, pid: str, idx: int) -> bytes:
        """Ảnh gallery đã thu nhỏ (JPEG) — ảnh gốc 3024x4032 quá nặng qua 4G. Đọc bằng imdecode (đường dẫn Unicode)."""
        import cv2
        import numpy as np

        files = self._gallery_files(pid)
        if not 0 <= idx < len(files):
            raise _err(404, "NOT_FOUND", "Không thấy ảnh gallery")
        img = cv2.imdecode(np.fromfile(str(files[idx]), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise _err(404, "NOT_FOUND", "Không đọc được ảnh gallery")
        h, w = img.shape[:2]
        scale = self.GALLERY_MAX_SIDE / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            raise _err(500, "INTERNAL_ERROR", "Không mã hoá được ảnh")
        return buf.tobytes()

    def admin_test_evidence(self, sess: Session, data: bytes) -> dict:
        """Thử bằng chứng trên một ảnh: chạy pipeline (không lưu), báo từng vật: SKU, trạng thái, chữ OCR đọc được,
        từ khoá catalog khớp, màu đo được + màu tham chiếu gần nhất, mã vạch đọc được + SKU trùng barcode."""
        import cv2
        import numpy as np

        self._need_admin(sess)
        if self._validating:
            raise _err(503, "SYSTEM_BUSY", "Hệ thống đang kiểm định, thử lại sau")
        if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
            raise _err(400, "IMAGE_DECODE_ERROR", "Không giải mã được ảnh")
        folder = self.s.media_root / "_test"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"test_{uuid.uuid4().hex[:10]}.jpg"
        path.write_bytes(data)
        try:
            fut = self._infer_pool.submit(self.executor.infer, str(path), self.get_setting("similarity_threshold", None),
                                          self.get_setting("min_confidence_accept", None))
            out = fut.result(timeout=self.s.job_timeout_seconds)
        except cf.TimeoutError as exc:
            raise _err(504, "PIPELINE_TIMEOUT", "Nhận diện quá lâu") from exc
        finally:
            path.unlink(missing_ok=True)
        repo = self.catalog.repo
        kw = {pid: repo.ocr_keywords(pid) for pid in repo.products()}
        colors = repo.color_references()
        barcodes = {(p.barcode or ""): pid for pid, p in repo.products().items() if p.barcode}
        items = []
        for d in out.detections:
            ev = d.plugin_evidence or {}
            ocr_text = str((ev.get("ocr") or {}).get("text") or "")
            norm = normalize_ocr_token(ocr_text)
            hits = sorted({pid for pid, toks in kw.items() for t in toks if t and t in norm}, key=lambda x: (len(x), x))
            rgb = (ev.get("color") or {}).get("dominant_rgb")
            near = None
            if rgb and colors:
                code, dist = min(((c, sum((float(a) - b) ** 2 for a, b in zip(rgb, v)) ** 0.5) for c, v in colors.items()),
                                 key=lambda t: t[1])
                near = {"code": code, "rgb_distance": round(dist, 1)}
            codes = [str(b.get("data") if isinstance(b, dict) else b) for b in ((ev.get("barcode") or {}).get("barcodes") or [])]
            p = self.catalog.get(d.product_id)
            items.append({"product_id": d.product_id, "name": p["name"] if p else f"SKU {d.product_id}", "status": d.status,
                          "bbox": [int(round(float(v))) for v in d.bbox], "ocr_text": ocr_text, "ocr_keyword_hits": hits,
                          "color_hex": rgb_to_hex(*[int(round(float(v))) for v in rgb]) if rgb else None, "color_nearest": near,
                          "barcodes": codes, "barcode_skus": [barcodes[c] for c in codes if c in barcodes],
                          "plugins": sorted(ev.keys())})
        return {"items": items, "detected_count": out.detected_count, "processing_time_ms": out.processing_time_ms}

    # Kiểm định: chạy trên luồng suy luận (dùng lại pipeline đang nạp). Trong lúc chạy, lượt chụp bị từ chối (503).
    def _baseline_report_path(self) -> Path:
        root = Path(self.s.pipeline_config).resolve().parent.parent
        name = "demo/report.json" if Path(self.s.pipeline_config).name == "config.demo.yaml" else "report.json"
        return root / "data" / "baseline" / name

    @staticmethod
    def _gate_metrics(rep: dict) -> dict:
        return {"f1": (rep.get("end_to_end") or {}).get("f1"), "fusion_accuracy": (rep.get("fusion") or {}).get("accuracy_after")}

    def admin_validation_status(self, sess: Session) -> dict:
        self._need_admin(sess)
        st = dict(self._validation)
        base = self._baseline_report_path()
        if base.is_file():
            st["baseline"] = self._gate_metrics(json.loads(base.read_text(encoding="utf-8")))
        return st

    def admin_start_validation(self, sess: Session, body: dict) -> dict:
        self._need_admin(sess)
        if body.get("confirm") is not True:
            raise _err(422, "CONFIRM_REQUIRED", "Cần xác nhận: hệ thống ngừng nhận diện trong lúc kiểm định (có thể 20–50 phút)")
        self._verify_advanced_password(sess, body.get("advanced_password"))
        validate = getattr(self.executor, "validate", None)
        if validate is None:
            raise _err(409, "VALIDATION_UNSUPPORTED", "Bộ suy luận hiện tại không hỗ trợ kiểm định")
        with self._lock:
            if self._validating or self._reloading:
                raise _err(409, "SYSTEM_BUSY", "Đang kiểm định hoặc đang áp dụng thiết lập")
            self._validating = True
            self._validation = {"status": "running", "started_at": utcnow(), "by": sess.username}
        out_dir = self.s.media_root.parent / "validation_web" / datetime.now().strftime("%Y%m%d-%H%M%S")

        def job() -> None:
            try:
                rep = validate(None, out_dir)
                self._validation = {**self._validation, "status": "done", "finished_at": utcnow(),
                                    "result": self._gate_metrics(rep), "report_dir": str(out_dir)}
            except Exception as exc:  # báo lại cho admin, không làm sập luồng suy luận
                log.exception("Kiểm định thất bại")
                self._validation = {**self._validation, "status": "error", "finished_at": utcnow(), "error": str(exc)[:300]}
            finally:
                self._validating = False

        self._infer_pool.submit(job)
        return self.admin_validation_status(sess)

    # ------------------------------------------------------------------ quản trị: thiết lập NÂNG CAO của pipeline
    RELOAD_TIMEOUT_S = 900

    def _config_overrides(self) -> dict:
        with self.db.read() as c:
            return {r["key"]: json.loads(r["value_json"]) for r in c.execute("SELECT key, value_json FROM config_overrides")}

    def _verify_advanced_password(self, sess: Session, password) -> None:
        """Mật khẩu NÂNG CAO (khác mật khẩu đăng nhập) — chặn dò bằng LoginLimiter theo tài khoản."""
        key = ("advanced", str(sess.user_id))
        wait = self.limiter.check(key)
        if wait:
            raise _err(429, "RATE_LIMITED", "Nhập sai mật khẩu nâng cao quá nhiều lần, thử lại sau", retry_after=wait)
        stored = self.get_setting("advanced_password_hash", None)
        if not stored:
            raise _err(409, "ADVANCED_PASSWORD_NOT_SET",
                       "Chưa có mật khẩu nâng cao — chạy trên máy chủ: python scripts/reset_password.py --advanced")
        if not isinstance(password, str) or not verify_password(password, stored):
            self.limiter.fail(key)
            raise _err(403, "ADVANCED_PASSWORD_INVALID", "Sai mật khẩu nâng cao")
        self.limiter.reset(key)

    def admin_config(self, sess: Session) -> dict:
        self._need_admin(sess)
        overrides = self._config_overrides()
        raw = read_raw_config(self.s.pipeline_config)
        try:
            eff, err = build_config(self.s.pipeline_config, overrides), None
        except ValueError as exc:  # ghi đè cũ không còn hợp lệ với YAML hiện tại: báo rõ, không tự xoá
            eff, err = None, str(exc)
        items = [{**e.to_dict(), "default": get_path(raw, e.key), "overridden": e.key in overrides,
                  "value": get_path(eff, e.key) if eff is not None else overrides.get(e.key, get_path(raw, e.key))}
                 for e in REGISTRY]
        return {"items": items, "reloading": self._reloading, "config_error": err,
                "advanced_password_set": bool(self.get_setting("advanced_password_hash", None)),
                "pipeline_config": Path(self.s.pipeline_config).name}

    def admin_apply_config(self, sess: Session, body: dict) -> dict:
        """Áp dụng thay đổi thiết lập nâng cao: kiểm mật khẩu nâng cao -> kiểm từng giá trị -> validate toàn bộ
        AppConfig -> nạp lại pipeline trên luồng suy luận (tuần tự với các lượt chụp) -> lưu ghi đè + change_log.
        Nạp lỗi -> executor tự quay về thiết lập cũ, DB không đổi."""
        self._need_admin(sess)
        if body.get("confirm") is not True:
            raise _err(422, "CONFIRM_REQUIRED", "Cần xác nhận: thay đổi ảnh hưởng nhận diện và tạm dừng hệ thống 30–60 giây")
        self._verify_advanced_password(sess, body.get("advanced_password"))
        changes = body.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise _err(422, "VALIDATION_ERROR", "Không có thay đổi nào")
        old, raw = self._config_overrides(), read_raw_config(self.s.pipeline_config)
        new = dict(old)
        for k, v in changes.items():
            entry = BY_KEY.get(k)
            if entry is None:
                raise _err(422, "VALIDATION_ERROR", f"Thiết lập không được phép sửa trên web: {k}")
            if v is None:  # về giá trị gốc
                new.pop(k, None)
                continue
            try:
                v = normalize_value(entry, v)
            except ValueError as exc:
                raise _err(422, "VALIDATION_ERROR", str(exc)) from exc
            if v == get_path(raw, k):
                new.pop(k, None)
            else:
                new[k] = v
        if new == old:
            return {**self.admin_config(sess), "applied": False}
        try:
            build_config(self.s.pipeline_config, new)
        except ValueError as exc:
            raise _err(422, "CONFIG_INVALID", str(exc)) from exc
        reload = getattr(self.executor, "reload_pipeline", None)
        if reload is None:
            raise _err(409, "RELOAD_UNSUPPORTED", "Bộ suy luận hiện tại không hỗ trợ nạp lại")
        if not self._reload_lock.acquire(blocking=False):
            raise _err(409, "RELOAD_IN_PROGRESS", "Đang áp dụng thiết lập khác, thử lại sau")
        try:
            self._reloading = True
            fut = self._infer_pool.submit(reload, new)  # cùng luồng với suy luận: không chạy song song lượt chụp
            try:
                fut.result(timeout=self.RELOAD_TIMEOUT_S)
            except cf.TimeoutError as exc:
                raise _err(504, "RELOAD_TIMEOUT", "Nạp lại pipeline quá lâu — xem log server, có thể cần khởi động lại") from exc
            except Exception as exc:
                log.exception("Áp dụng thiết lập nâng cao thất bại")
                raise _err(500, "RELOAD_FAILED", f"Không nạp được pipeline với thiết lập mới, đã quay về thiết lập cũ: {exc}") from exc
        finally:
            self._reloading = False
            self._reload_lock.release()
        now = utcnow()
        with self.db.tx() as c:
            for k in sorted(set(old) | set(new)):
                if old.get(k) == new.get(k) and (k in old) == (k in new):
                    continue
                if k in new:
                    c.execute("INSERT INTO config_overrides(key,value_json,updated_by,updated_at) VALUES(?,?,?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_by=excluded.updated_by, "
                              "updated_at=excluded.updated_at", (k, json.dumps(new[k]), sess.username, now))
                else:
                    c.execute("DELETE FROM config_overrides WHERE key=?", (k,))
                c.execute("INSERT INTO change_log(table_name,record_id,field_name,old_value,new_value,changed_by,changed_at) "
                          "VALUES('config',?,?,?,?,?,?)", (k, k, json.dumps(old[k]) if k in old else None,
                                                           json.dumps(new[k]) if k in new else None, sess.user_id, now))
        log.info("Đã áp dụng thiết lập nâng cao: %s", {k: new.get(k) for k in changes})
        return {**self.admin_config(sess), "applied": True}

    # ------------------------------------------------------------------ quản trị: báo cáo, nhân viên
    def admin_reports(self, sess: Session, rng: str = "today") -> dict:
        self._need_admin(sess)
        if rng not in ("today", "7d", "30d"):
            raise _err(422, "VALIDATION_ERROR", "range phải là today|7d|30d")
        start = self._range_start("today")
        with self.db.read() as c:
            paid = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total_amount),0) t FROM orders WHERE status='paid' AND created_at>=?", (start,)).fetchone()
            cap = c.execute("SELECT COUNT(*) n, SUM(status='error') e, AVG(CASE WHEN status='success' THEN processing_time_ms END) a "
                            "FROM captures WHERE created_at>=?", (start,)).fetchone()
            active = c.execute("SELECT COUNT(*) FROM shifts WHERE ended_at IS NULL").fetchone()[0]
            staff = c.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
            prices, barcodes = self._prices(c), self._barcodes(c)
        n = cap["n"] or 0
        extra = self._range_report(rng)
        return {**extra, "orders_today": paid["n"], "revenue_today": paid["t"], "captures_today": n,
                "error_rate": round((cap["e"] or 0) / n, 4) if n else 0.0,
                "avg_processing_ms": round(cap["a"], 1) if cap["a"] else None,
                "active_shifts": active, "active_staff": staff, "queue_size": self._queue.qsize(),
                "products_total": len(self.catalog.all()),
                "products_missing_price": sum(1 for p in self.catalog.all() if p not in prices),
                "products_missing_barcode": sum(1 for p in self.catalog.all() if not barcodes.get(p))}

    def _range_report(self, rng: str) -> dict:
        """Doanh thu theo ngày (giờ địa phương, đủ cả ngày không có đơn) và top 5 sản phẩm bán chạy trong khoảng."""
        start = self._range_start(rng)
        off = timedelta(hours=self.s.tz_offset_hours)
        n_days = {"today": 1, "7d": 7, "30d": 30}[rng]
        with self.db.read() as c:
            rows = c.execute("SELECT paid_at, total_amount FROM orders WHERE status='paid' AND paid_at>=?", (start,)).fetchall()
            tops = c.execute("SELECT i.product_id pid, SUM(i.quantity) q, SUM(i.quantity*COALESCE(i.unit_price,0)) r "
                             "FROM order_items i JOIN orders o ON o.id=i.order_id WHERE o.status='paid' AND o.paid_at>=? "
                             "GROUP BY i.product_id ORDER BY q DESC, r DESC, i.product_id LIMIT 5", (start,)).fetchall()
        today = (datetime.now(timezone.utc) + off).date()
        daily = {(today - timedelta(days=k)).isoformat(): [0, 0] for k in range(n_days - 1, -1, -1)}
        for r in rows:
            day = (datetime.fromisoformat(r["paid_at"]) + off).date().isoformat()
            if day in daily:
                daily[day][0] += 1
                daily[day][1] += r["total_amount"] or 0
        return {"range": rng, "range_orders": sum(v[0] for v in daily.values()), "range_revenue": sum(v[1] for v in daily.values()),
                "daily": [{"date": d, "orders": v[0], "revenue": v[1]} for d, v in daily.items()],
                "top_products": [{"product_id": t["pid"], "name": (self.catalog.get(t["pid"]) or {}).get("name", f"SKU {t['pid']}"),
                                  "quantity": t["q"], "revenue": t["r"]} for t in tops]}

    # ------------------------------------------------------------------ quản trị: nhật ký thay đổi + hoàn tác
    def admin_change_log(self, sess: Session, table: str = "", record: str = "", limit: int = 100) -> list[dict]:
        self._need_admin(sess)
        sql = ("SELECT l.*, u.username FROM change_log l LEFT JOIN users u ON u.id=l.changed_by WHERE 1=1")
        args: list = []
        if table:
            sql += " AND l.table_name=?"
            args.append(table)
        if record:
            sql += " AND l.record_id=?"
            args.append(record)
        sql += " ORDER BY l.id DESC LIMIT ?"
        args.append(max(1, min(500, limit)))
        with self.db.read() as c:
            return [{"id": r["id"], "table": r["table_name"], "record_id": r["record_id"], "field": r["field_name"],
                     "old": r["old_value"], "new": r["new_value"], "by": r["username"], "at": r["changed_at"],
                     "name": (self.catalog.get(r["record_id"]) or {}).get("name")
                     if r["table_name"] in ("product", "product_evidence") else None}
                    for r in c.execute(sql, args)]

    def admin_revert_change(self, sess: Session, lid: int, body: dict | None = None) -> dict:
        """Hoàn tác MỘT thay đổi (giá/barcode/cài đặt) bằng cách ghi lại giá trị cũ; việc hoàn tác cũng được ghi log.
        Từ chối (409) nếu giá trị hiện tại không còn bằng giá trị mới của dòng log (đã bị đổi sau đó)."""
        self._need_admin(sess)
        with self.db.read() as c:
            row = c.execute("SELECT * FROM change_log WHERE id=?", (lid,)).fetchone()
            prices, barcodes = self._prices(c), self._barcodes(c)
        if not row:
            raise _err(404, "NOT_FOUND", "Không thấy dòng nhật ký")
        table, field, rid = row["table_name"], row["field_name"], row["record_id"]
        if table == "product" and field in ("price", "barcode"):
            if not self.catalog.get(rid):
                raise _err(404, "NOT_FOUND", "Sản phẩm không còn trong catalog")
            if field == "price":
                current = str(prices[rid]) if rid in prices else None
                expected, target = row["new_value"], (None if row["old_value"] is None else int(row["old_value"]))
            else:
                current, expected, target = barcodes.get(rid, "") or "", row["new_value"] or "", row["old_value"] or ""
            if current != expected:
                raise _err(409, "CHANGE_STALE", "Giá trị đã được thay đổi sau lần sửa này, không thể hoàn tác")
            self.admin_update_product(sess, rid, {field: target})
            return {"ok": True, "reverted": {"table": table, "record_id": rid, "field": field}}
        if table == "product" and field == "name":
            p = self.catalog.get(rid)
            if not p:
                raise _err(404, "NOT_FOUND", "Sản phẩm không còn trong catalog")
            if p["name"] != row["new_value"]:
                raise _err(409, "CHANGE_STALE", "Tên đã được thay đổi sau lần sửa này, không thể hoàn tác")
            self.admin_update_product(sess, rid, {"name": row["old_value"]})
            return {"ok": True, "reverted": {"table": table, "record_id": rid, "field": field}}
        if table == "product_evidence":
            with self.db.read() as c:
                cur = self._evidence_rows(c).get(rid, {}).get(field)
            if (None if cur is None else json.dumps(cur, ensure_ascii=False)) != row["new_value"]:
                raise _err(409, "CHANGE_STALE", "Bằng chứng đã được thay đổi sau lần sửa này, không thể hoàn tác")
            target = None if row["old_value"] is None else json.loads(row["old_value"])
            self.admin_update_evidence(sess, rid, {field: target, "confirm": True})
            return {"ok": True, "reverted": {"table": table, "record_id": rid, "field": field}}
        if table == "config":  # thiết lập nâng cao: cần mật khẩu nâng cao, nạp lại pipeline
            cur = self._config_overrides()
            if (json.dumps(cur[field]) if field in cur else None) != row["new_value"]:
                raise _err(409, "CHANGE_STALE", "Thiết lập đã được thay đổi sau lần sửa này, không thể hoàn tác")
            target = None if row["old_value"] is None else json.loads(row["old_value"])
            self.admin_apply_config(sess, {"changes": {field: target}, "confirm": True,
                                           "advanced_password": (body or {}).get("advanced_password")})
            return {"ok": True, "reverted": {"table": table, "record_id": rid, "field": field}}
        if table == "color_reference":
            cur = next((x["hex"] for x in self.admin_colors(sess) if x["code"] == rid), None)
            if cur != row["new_value"]:
                raise _err(409, "CHANGE_STALE", "Màu đã được thay đổi sau lần sửa này, không thể hoàn tác")
            self.admin_update_color(sess, rid, {"hex": row["old_value"], "confirm": True})
            return {"ok": True, "reverted": {"table": table, "record_id": rid, "field": field}}
        if table == "settings":
            current, expected = self.public_settings().get(field), json.loads(row["new_value"])
            if current != expected:
                raise _err(409, "CHANGE_STALE", "Cài đặt đã được thay đổi sau lần sửa này, không thể hoàn tác")
            self.update_settings(sess, {field: json.loads(row["old_value"])})
            return {"ok": True, "reverted": {"table": table, "record_id": rid, "field": field}}
        raise _err(422, "VALIDATION_ERROR", "Loại thay đổi này không hoàn tác được")

    def admin_users(self, sess: Session) -> list[dict]:
        self._need_admin(sess)
        with self.db.read() as c:
            return [{"id": r["id"], "username": r["username"], "full_name": r["full_name"], "role": r["role"],
                     "is_active": bool(r["is_active"]),
                     "online": bool(c.execute("SELECT 1 FROM shifts WHERE user_id=? AND ended_at IS NULL", (r["id"],)).fetchone())}
                    for r in c.execute("SELECT * FROM users ORDER BY id")]

    def admin_create_user(self, sess: Session, body: dict) -> dict:
        self._need_admin(sess)
        username = str(body.get("username", "")).strip().lower()
        password, role = str(body.get("password", "")), body.get("role", "staff")
        if not _USERNAME_RE.match(username):
            raise _err(422, "VALIDATION_ERROR", "Tài khoản 3–32 ký tự (chữ, số, . _ -)")
        if len(password) < 8:
            raise _err(422, "VALIDATION_ERROR", "Mật khẩu tối thiểu 8 ký tự")
        if role not in ("staff", "admin"):
            raise _err(422, "VALIDATION_ERROR", "Vai trò phải là staff hoặc admin")
        try:
            with self.db.tx() as c:
                uid = c.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES(?,?,?,?,?)",
                                (username, hash_password(password), str(body.get("full_name", ""))[:80], role, utcnow())).lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise _err(409, "USER_EXISTS", "Tài khoản đã tồn tại") from exc
            raise
        return next(u for u in self.admin_users(sess) if u["id"] == uid)

    def admin_update_user(self, sess: Session, uid: int, body: dict) -> dict:
        self._need_admin(sess)
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                raise _err(404, "NOT_FOUND", "Không thấy nhân viên")
            if "password" in body:
                if len(str(body["password"])) < 8:
                    raise _err(422, "VALIDATION_ERROR", "Mật khẩu tối thiểu 8 ký tự")
                c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(str(body["password"])), uid))
                c.execute("UPDATE shifts SET ended_at=? WHERE user_id=? AND ended_at IS NULL", (utcnow(), uid))
            if "full_name" in body:
                c.execute("UPDATE users SET full_name=? WHERE id=?", (str(body["full_name"])[:80], uid))
            if "is_active" in body:
                if not isinstance(body["is_active"], bool):
                    raise _err(422, "VALIDATION_ERROR", "is_active phải là true/false")
                if uid == sess.user_id and not body["is_active"]:
                    raise _err(409, "VALIDATION_ERROR", "Không thể tự khoá tài khoản của mình")
                c.execute("UPDATE users SET is_active=? WHERE id=?", (int(body["is_active"]), uid))
                if not body["is_active"]:
                    c.execute("UPDATE shifts SET ended_at=? WHERE user_id=? AND ended_at IS NULL", (utcnow(), uid))
        return next(u for u in self.admin_users(sess) if u["id"] == uid)
