"""The catalog: product search for the POS screen (by name without Vietnamese accents, by id, by
barcode), and the admin's edits of prices, barcodes, names, recognition evidence and colour
references, each in the change log. After an edit of the engine's catalog the recognizer re-reads
it (`on_change`)."""

import asyncio
import io
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # no stubs installed; only safe_load is used
from PIL import Image, ImageOps

from app.core.config import CoreSettings
from app.core.errors import AppError, Conflict, Invalid, NotFound
from app.core.signed_url import signed_query, verify
from app.modules.audit.ports import ChangeEntry
from app.modules.audit.schemas import RevertIn
from app.modules.auth.ports import CurrentUser
from app.modules.catalog.ports import Product
from app.modules.catalog.repository import CatalogEdits, CatalogRepository
from app.modules.catalog.schemas import (
    CONFIRM_TEXT,
    EVIDENCE_FIELDS,
    AdminProductsOut,
    ColorOut,
    ColorPatch,
    EvidenceOut,
    EvidencePatch,
    ProductOut,
    ProductPatch,
)

SEARCH_LIMIT = 50
ADMIN_FILTERS = ("", "missing_price", "missing_barcode", "needs_naming", "out_of_stock")
GALLERY_LIMIT = 12
GALLERY_MAX_SIDE = 1024
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
_BARCODE = re.compile(r"^[0-9A-Za-z\-]{4,32}$")
_COLOR_CODE = re.compile(r"^[A-Z0-9_\-]{2,20}$")

OnChange = Callable[[], Awaitable[None]]


def fold(text: str) -> str:
    """Lower case without Vietnamese accents, so "phan ma hong" finds "Phấn má hồng"."""
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _catalog_order(product: Product) -> tuple[int, str]:
    # the engine's order: ids are numbers stored as text, so "2" comes before "10"
    return (len(product.id), product.id)


def _out_of_stock(product: Product) -> bool:
    return product.stock is not None and product.stock <= 0


def _stale(what: str) -> Conflict:
    return Conflict(f"{what} đã được thay đổi sau lần sửa này, không thể hoàn tác", code="CHANGE_STALE")


def _color_code(raw: Any) -> str:
    code = str(raw).strip().upper()
    if not _COLOR_CODE.match(code):
        raise Invalid("Mã màu 2–20 ký tự chữ hoa, số, '_' hoặc '-'")
    return code


def _require_confirm(confirm: Any) -> None:
    if confirm is not True:
        raise AppError(
            f'Cần xác nhận: "{CONFIRM_TEXT}"', code="CONFIRM_REQUIRED", status_code=422, confirm_text=CONFIRM_TEXT
        )


class CatalogService:
    def __init__(self, repo: CatalogRepository, edits: CatalogEdits, settings: CoreSettings):
        self.repo, self.edits, self.settings = repo, edits, settings
        self._on_change: list[OnChange] = []

    def on_change(self, callback: OnChange) -> None:
        """Run after every edit of the engine's catalog (the recognizer re-reads it)."""
        self._on_change.append(callback)

    async def _changed(self) -> None:
        for callback in self._on_change:
            await callback()

    # ---------------------------------------------------------------- reading

    async def lookup(self, product_ids: Iterable[str]) -> dict[str, Product]:
        """Products by id, including the ones no longer on sale. An unknown id is absent."""
        return await self.repo.products_by_id(product_ids)

    async def search(self, search: str = "", barcode: str = "") -> list[Product]:
        """Products on sale, at most `SEARCH_LIMIT`. A barcode must match exactly and wins over
        `search`; `search` matches a part of the name or the whole id; neither lists the catalog."""
        barcode, query = barcode.strip(), fold(search.strip())
        found = []
        for product in sorted(await self.repo.active_products(), key=_catalog_order):
            if barcode:
                if product.barcode != barcode:
                    continue
            elif query and query not in fold(product.name) and query != product.id:
                continue
            found.append(product)
            if len(found) >= SEARCH_LIMIT:
                break
        return found

    # ---------------------------------------------------------------- admin: products

    async def admin_list(self, search: str = "", filter_: str = "", page: int = 1, size: int = 50) -> AdminProductsOut:
        """The catalog on sale for the admin screen: search by name, id or barcode (a part of any),
        filter what still needs work, one page at a time."""
        if filter_ not in ADMIN_FILTERS:
            raise Invalid("filter phải là missing_price|missing_barcode|needs_naming|out_of_stock")
        products = sorted(await self.repo.active_products(), key=_catalog_order)
        query = fold(search.strip())
        rows = [
            p
            for p in products
            if (not query or query in fold(p.name) or query in p.id or query in p.barcode)
            and (filter_ != "missing_price" or p.price is None)
            and (filter_ != "missing_barcode" or not p.barcode)
            and (filter_ != "needs_naming" or p.needs_naming)
            and (filter_ != "out_of_stock" or _out_of_stock(p))
        ]
        page, size = max(1, page), max(1, min(200, size))
        return AdminProductsOut(
            total=len(rows),
            page=page,
            size=size,
            items=[ProductOut(**asdict(p)) for p in rows[(page - 1) * size : page * size]],
            missing_price=sum(1 for p in products if p.price is None),
            missing_barcode=sum(1 for p in products if not p.barcode),
            needs_naming=sum(1 for p in products if p.needs_naming),
            out_of_stock=sum(1 for p in products if _out_of_stock(p)),
        )

    async def _existing(self, product_id: str, missing: str = "Không thấy sản phẩm") -> Product:
        product = (await self.lookup([product_id])).get(product_id)
        if product is None:
            raise NotFound(missing)
        return product

    async def _update_product(self, current: CurrentUser, product_id: str, fields: dict[str, Any]) -> ProductOut:
        if "barcode" in fields:
            barcode = (fields["barcode"] or "").strip()
            if barcode and not _BARCODE.match(barcode):
                raise Invalid("Barcode gồm 4–32 ký tự chữ, số hoặc dấu gạch ngang")
            fields["barcode"] = barcode or None
        if "name" in fields:
            name = " ".join((fields["name"] or "").split())
            if not 1 <= len(name) <= 120:
                raise Invalid("Tên sản phẩm 1–120 ký tự")
            fields["name"] = name
        if await asyncio.to_thread(self.edits.update_product, product_id, fields, current.user_id):
            await self._changed()  # price and stock are the web's: they do not touch the recognizer
        return ProductOut(**asdict(await self._existing(product_id)))

    async def update(self, current: CurrentUser, product_id: str, body: ProductPatch) -> ProductOut:
        await self._existing(product_id)
        fields: dict[str, Any] = {
            name: getattr(body, name) for name in ("price", "stock", "barcode", "name") if name in body.model_fields_set
        }
        if not fields:
            raise Invalid("Chỉ sửa được 'price', 'stock', 'barcode' và 'name'")
        return await self._update_product(current, product_id, fields)

    # ---------------------------------------------------------------- admin: evidence and colours

    def _pipeline_config(self) -> dict[str, Any]:
        """The engine's YAML: where the gallery is, how short an OCR keyword may be. Read at each
        use, so it follows the file the engine runs with."""
        with open(self.settings.pipeline_config, encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream) or {}
        return loaded if isinstance(loaded, dict) else {}

    def _ocr_min_length(self) -> int:
        return int(((self._pipeline_config().get("plugins") or {}).get("ocr") or {}).get("min_text_length", 3))

    def _normalize(self, product_id: str, field: str, value: Any, ocr_min_length: int) -> Any:
        """A value as the engine stores it; empty = None (remove the evidence)."""
        if value is None or value == "" or value == []:
            return None
        if field == "color_code":
            return _color_code(value)
        if not isinstance(value, list) or not all(isinstance(v, str | int) and not isinstance(v, bool) for v in value):
            raise Invalid(f"'{field}' phải là danh sách")
        if field == "ocr_keywords":
            from engine.catalog.validation import normalize_ocr_keywords

            try:
                return normalize_ocr_keywords([str(v) for v in value], ocr_min_length) or None
            except ValueError as exc:
                raise Invalid(str(exc)) from exc
        if field == "force_evidence":
            from engine.catalog.validation import FORCE_EVIDENCE_PLUGINS

            bad = sorted({str(v) for v in value} - FORCE_EVIDENCE_PLUGINS)
            if bad:
                raise Invalid(f"Plugin không hợp lệ: {bad} (chỉ ocr, color, barcode)")
            return sorted({str(v) for v in value})
        ids = sorted({str(v).strip() for v in value} - {""}, key=lambda x: (len(x), x))
        if product_id in ids:
            raise Invalid("Không thể đặt sản phẩm dễ nhầm với chính nó")
        return ids or None

    async def colors(self) -> list[ColorOut]:
        """Every colour reference with the products that name it, then the colours products name
        that have no reference yet."""
        used: dict[str, list[str]] = {}
        for pid, evidence in sorted((await self.repo.evidence()).items(), key=lambda kv: (len(kv[0]), kv[0])):
            if evidence.get("color_code"):
                used.setdefault(str(evidence["color_code"]), []).append(pid)
        references = await self.repo.colors()
        known = {c.code for c in references}
        out = [ColorOut(**asdict(c), used_by=used.get(c.code, []), missing=False) for c in references]
        out += [
            ColorOut(code=code, hex=None, used_by=pids, missing=True)
            for code, pids in sorted(used.items())
            if code not in known
        ]
        return out

    async def evidence(self, product_id: str, warnings: list[str] | None = None) -> EvidenceOut:
        product = await self._existing(product_id)
        mine = (await self.repo.evidence(product_id)).get(product_id, {})
        return EvidenceOut(
            product=ProductOut(**asdict(product)),
            evidence={f: mine.get(f, None if f == "color_code" else []) for f in EVIDENCE_FIELDS},
            colors=await self.colors(),
            confirm_text=CONFIRM_TEXT,
            ocr_min_length=await asyncio.to_thread(self._ocr_min_length),
            gallery=await self.gallery_urls(product_id),
            warnings=warnings or [],
        )

    async def _update_evidence(self, current: CurrentUser, product_id: str, raw: dict[str, Any]) -> list[str]:
        ocr_min_length = await asyncio.to_thread(self._ocr_min_length)
        fields = {f: self._normalize(product_id, f, value, ocr_min_length) for f, value in raw.items()}
        warnings, changed = await asyncio.to_thread(
            self.edits.update_evidence, product_id, fields, current.user_id, current.username, ocr_min_length
        )
        if changed:
            await self._changed()
        if any(not token.isascii() for token in (fields.get("ocr_keywords") or [])):
            warnings.append("Từ khoá có ký tự ngoài bảng chữ Latin — OCR hiện đọc tiếng Anh nên có thể không đọc ra")
        return warnings

    async def update_evidence(self, current: CurrentUser, product_id: str, body: EvidencePatch) -> EvidenceOut:
        """Change a product's recognition evidence. The admin must confirm they know it changes the
        recognition; `confusable_with` is written on both products of a pair."""
        await self._existing(product_id)
        _require_confirm(body.confirm)
        raw = {f: getattr(body, f) for f in EVIDENCE_FIELDS if f in body.model_fields_set}
        if not raw:
            raise Invalid(f"Cần ít nhất một trường: {', '.join(EVIDENCE_FIELDS)}")
        warnings = await self._update_evidence(current, product_id, raw)
        return await self.evidence(product_id, warnings)

    async def _set_color(self, current: CurrentUser, code: str, hex_value: str | None) -> ColorOut:
        if hex_value is not None:
            from engine.catalog.validation import parse_hex, rgb_to_hex

            try:
                hex_value = rgb_to_hex(*parse_hex(hex_value))
            except ValueError as exc:
                raise Invalid(str(exc)) from exc
        if await asyncio.to_thread(self.edits.update_color, code, hex_value, current.user_id):
            await self._changed()
        found = next((c for c in await self.colors() if c.code == code), None)
        return found or ColorOut(code=code, hex=None, used_by=[], missing=False)

    async def update_color(self, current: CurrentUser, code: str, body: ColorPatch) -> ColorOut:
        """Create, change or (hex null) remove a colour reference. Stored as RGB + hex; the Reranker
        converts to Lab itself."""
        _require_confirm(body.confirm)
        code = _color_code(code)
        if "hex" not in body.model_fields_set:
            raise Invalid("Cần trường 'hex' (#RRGGBB, hoặc null để xoá)")
        return await self._set_color(current, code, body.hex)

    # ---------------------------------------------------------------- gallery photos

    def _gallery_files(self, folder: str | None) -> list[Path]:
        if not folder:
            return []
        rel = (self._pipeline_config().get("paths") or {}).get("gallery_dir")
        if not rel:
            return []
        # relative gallery paths in the engine's YAML are relative to backend/ (the config's grandparent)
        base = Path(rel) if Path(rel).is_absolute() else self.settings.pipeline_config.resolve().parent.parent / rel
        directory = base / folder
        if not directory.is_dir():
            return []
        return sorted(f for f in directory.iterdir() if f.suffix.lower() in _IMAGE_SUFFIXES)

    async def gallery_urls(self, product_id: str) -> list[str]:
        files = await asyncio.to_thread(self._gallery_files, await self.repo.gallery_folder(product_id))
        urls = []
        for index in range(min(len(files), GALLERY_LIMIT)):
            rel = f"g/{product_id}/{index}"
            query = signed_query(self.settings.media_url_secret, rel, self.settings.media_url_ttl_seconds)
            urls.append(f"/api/gallery/{product_id}/{index}?{query}")
        return urls

    async def gallery_image(self, product_id: str, index: int, expires: int, signature: str) -> bytes:
        """A gallery photo scaled down to `GALLERY_MAX_SIDE` (the originals are 3024x4032: too heavy
        over 4G), as JPEG. For whoever holds a URL the API signed."""
        if not verify(self.settings.media_url_secret, f"g/{product_id}/{index}", expires, signature):
            raise AppError("Liên kết ảnh không hợp lệ hoặc đã hết hạn", code="FORBIDDEN", status_code=403)
        files = await asyncio.to_thread(self._gallery_files, await self.repo.gallery_folder(product_id))
        if not 0 <= index < len(files):
            raise NotFound("Không thấy ảnh gallery")
        return await asyncio.to_thread(self._scaled_jpeg, files[index])

    @staticmethod
    def _scaled_jpeg(path: Path) -> bytes:
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
        except (OSError, ValueError) as exc:
            raise NotFound("Không đọc được ảnh gallery") from exc
        image.thumbnail((GALLERY_MAX_SIDE, GALLERY_MAX_SIDE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=88)
        return out.getvalue()

    # ---------------------------------------------------------------- change-log reverters

    async def revert_product(self, current: CurrentUser, entry: ChangeEntry, _: RevertIn) -> None:
        """Reverter for the `product` table: price, stock, barcode, name."""
        product = await self._existing(entry.record_id, "Sản phẩm không còn trong catalog")
        if entry.field == "price":
            if (None if product.price is None else str(product.price)) != entry.new:
                raise _stale("Giá trị")
            target: Any = None if entry.old is None else int(entry.old)
        elif entry.field == "stock":
            # a sale since then moved the quantity: the count that was typed no longer applies
            if (None if product.stock is None else str(product.stock)) != entry.new:
                raise _stale("Tồn kho")
            target = None if entry.old is None else int(entry.old)
        elif entry.field == "barcode":
            if product.barcode != (entry.new or ""):
                raise _stale("Giá trị")
            target = entry.old or ""
        elif entry.field == "name":
            if product.name != entry.new:
                raise _stale("Tên")
            target = entry.old
        else:
            raise Invalid("Loại thay đổi này không hoàn tác được")
        await self._update_product(current, entry.record_id, {entry.field: target})

    async def revert_evidence(self, current: CurrentUser, entry: ChangeEntry, _: RevertIn) -> None:
        """Reverter for `product_evidence` (a pair comes back on both products)."""
        await self._existing(entry.record_id, "Sản phẩm không còn trong catalog")
        value = (await self.repo.evidence(entry.record_id)).get(entry.record_id, {}).get(entry.field)
        if (None if value is None else json.dumps(value, ensure_ascii=False)) != entry.new:
            raise _stale("Bằng chứng")
        await self._update_evidence(
            current, entry.record_id, {entry.field: None if entry.old is None else json.loads(entry.old)}
        )

    async def revert_color(self, current: CurrentUser, entry: ChangeEntry, _: RevertIn) -> None:
        """Reverter for `color_reference`."""
        found = next((c for c in await self.colors() if c.code == entry.record_id), None)
        if (found.hex if found else None) != entry.new:
            raise _stale("Màu")
        await self._set_color(current, entry.record_id, entry.old)
