# **STOCKTAKING AI**

## **MODULE SPECIFICATION**

Version: 0.1.0 (extended)

# **Purpose**

Defines the formal boundary, responsibility, data contract, public API, and forbidden behaviors for every module. Implementation details belong to the source code itself.

# **Overall Module Relationship Flow**
```text
UI / CLI / Entry Points
         │
         ▼
Inference / Validation Runners
         │
         ▼
InventoryPipeline (Central Orchestrator)
         │
         ├──────────┬───────────┬─────────────┐
         ▼          ▼           ▼             ▼
    Detection  OverlapResolver  Refiner    Cropper
         │          │           │             │
         └──────────┴─────┬─────┴─────────────┴────────┐
                          ▼                            ▼
                    DecisionEngine                 Retrieval
                          │                            │
                          ▼                            │
                    PluginManager (on-demand)          │
                          │                            │
                          └─────────────┬──────────────┘
                                        ▼
                                     Reranker
                                        │
                                        ▼
                                  StorageManager
```

Only `InventoryPipeline` coordinates data flow between AI modules.

# **1\. core/**
**Files**: `config.py`, `logger.py`, `utils.py`, `color_signature.py`
**Responsibilities**: load/validate `configs/config.yaml` into typed Pydantic models; structured logging; generic filesystem/image helpers; colour signatures ((a\*, b\*) histogram of a crop, shared by the Color plugin, `scripts/build_color_signatures.py` and the Reranker).
**Public APIs**: `load_config(config_path=None) -> AppConfig` (cached), `reload_config(config_path=None) -> AppConfig`, `read_raw_config(config_path) -> dict`, `build_config(config_path, overrides=None) -> AppConfig` (uncached; applies `{"dotted.key": value}` overrides before validation — used by the web backend's advanced settings), `setup_logger(name) -> Logger`, `get_logger(name) -> Logger`
**Forbidden**: no AI/ML inference logic, no UI logic.

# **1b\. catalog/**
**Files**: `db.py`, `repository.py`, `snapshot.py`, `factory.py`, `validation.py`, `migrate.py`, `reconcile.py`, `sync_gallery.py`, `checks.py`, `edits.py`
**Responsibilities**: the product catalog (SKUs, recognition evidence, colour references) stored in a database (a SQLite file, or Postgres under the web POS), and the read-only `CatalogRepository` through which every pipeline module reads it. `edits.py` holds the catalog writes used by the web admin (barcode, name, evidence, reference colour): they run on the caller's `Session` and never commit, so the web writes its change log on the same connection and commits once. The source is chosen by `catalog.source` (`sqlite` | `snapshot` | `database`) with no silent fallback. Full description: `docs/04_DATA_AND_CATALOG.md`.
**Forbidden**: no model inference; pipeline modules never open the database directly — only through `CatalogRepository`.
# **2\. models/**
**Files**: `models.py`

**Key DTOs**: `BoundingBox`, `ImageData`, `Detection`/`DetectionResult`, `OverlapPair`/`OverlapGroup`/`OverlapResult`, `RefinedBox`/`RefinementResult`, `CropImage` (dual-resolution — see below), `RetrievalCandidate`/`RetrievalResult`, `DecisionResult`, `PluginResult`, `InventoryItem`/`InventoryResult`, `CropTrace`/`PipelineTrace`.

**`CropImage` contract**:

```Python

image_array: np.ndarray       # resized (cropping.target_size) — Retriever only

raw_image_array: np.ndarray   # original resolution, clip+pad only — Plugins only
```
Both fields are always populated by `Cropper`; no other module may resize either array after the fact.

**`RefinementResult` contract**: independent from `DetectionResult` by design — `Detector`'s output contract must never be overwritten by the optional segmentation stage. `Cropper` is the only module that reads both together.

**`PipelineTrace`**: diagnostics-only, produced solely by `InventoryPipeline.run_with_trace()`; never produced by `run()`.

**Forbidden**: no business logic, no AI/ML library imports.

# **3\. detection/**

## **3.1 detector.py**

Thin dispatcher. **Public API**: `Detector.detect(image_data: ImageData) -> DetectionResult`

Sorts the backend's boxes by confidence, caps them at `detection.max_detections`, then (when `detection.suppression.enabled`) drops redundant boxes via `postprocess.suppress_redundant` (see §3.4).

**Forbidden**: classification, retrieval, cropping, plugin execution, file I/O.

## **3.2 backends/**

`base.py` (abstract `DetectionBackend`), `mock_contour.py` (Canny + contours, no weights), `rf_detr.py` (real RF-DETR neural detector). Detection output is **class-agnostic by design** — product identity is never resolved here (see Retrieval).

## **3.3 cropper.py**

**Public API**:

```Python

Cropper.crop(image_data: ImageData, detection_result: DetectionResult, refinement_result: RefinementResult) -> list[CropImage]
```
For each detection: uses `RefinedBox.refined_bbox` when present, not a fallback, and `cropping.use_refined_bbox` is enabled; otherwise the original `Detection.bbox`. Produces both `image_array` (resized) and `raw_image_array` (original resolution) per crop.

**Forbidden**: detection, segmentation, or retrieval algorithms; mutating `DetectionResult`.

## **3.4 postprocess.py**

Pure geometric functions (RF-DETR has no suppression step of its own; one product returned as two boxes would be billed twice). Configured under `detection.suppression`:

* `suppress_redundant(...)` — called by `Detector`: of two boxes with IoU ≥ `duplicate_iou` (0.6) the less confident is dropped; a box enclosing ≥ `container_min_boxes` (3; 0 = off) other boxes, each at least `containment_ratio` (0.8) inside it, is dropped as a box around a whole group.  
* `drop_nested_same_product(...)` — called by `InventoryPipeline` after recognition (it needs the SKU): when two items of the same `product_id` are nested (smaller box ≥ `nested_same_product_ratio` (0.6) inside the larger; 0 = off), the item with the lower detection confidence is dropped.

**Forbidden**: no model calls; never looks at pixels.

# **4\. pipeline/overlap.py (OverlapResolver)**

Purely geometric. **Public API**:

```Python

OverlapResolver.resolve(detection_result: DetectionResult) -> OverlapResult

find_suspicious_pairs(detections: list[Detection], iou_threshold: float, overlap_ratio_threshold: float) -> list[OverlapPair]
```
`find_suspicious_pairs` is a pure module-level function — the single source of truth for "what counts as suspicious overlap," reused identically by `OverlapResolver` at runtime and by VAL's Overlap-stage evaluator, so the two can never silently disagree.

**Forbidden**: this is explicitly **not NMS** — never removes or mutates any `Detection`. Never decides which segmentation backend to use.

# **5\. segmentation/ (Refiner + backends)**

Optional stage, invoked only when `OverlapResolver` sets `needs_refinement=True`. **Public API**:

```Python

Refiner.refine(image_array: np.ndarray, detection_result: DetectionResult, overlap_result: OverlapResult) -> RefinementResult
```
`backends/base.py` (abstract `SegmentationBackend`), `mock_refiner.py` (passthrough fallback), `sam2.py` (real SAM2). The pipeline never imports a concrete backend directly — only `Refiner`.

**Forbidden**: never mutates `DetectionResult`.

# **6\. retrieval/**

## **6.1 retriever.py**

Runtime-only. **Public API**: `Retriever.retrieve(crop: CropImage) -> RetrievalResult`

Loads a *pre-built* FAISS index; product data comes from the shared `CatalogRepository` (see `docs/04_DATA_AND_CATALOG.md`). Never builds or rebuilds anything at runtime. `product_id` throughout this module (and everywhere else in the system) is always the stable internal numeric ID as a string — never a gallery folder name.

**Forbidden**: detection, cropping, OCR, barcode decoding, direct Storage/UI interaction.

## **6.2 backends/**

`base.py` (abstract `EmbeddingBackend`), `mock_visual_embedding.py` (HSV histogram, no weights), `siglip2.py` (real SigLIP2; embedding is mean-pooled across patch tokens before use — see Mandated Pooling rules).

## **6.3 gallery_builder.py**

Build-time only (invoked by `pipeline/build.py`). Never imported by runtime `Retriever`. Folder → `product_id` mapping comes from `CatalogRepository.folder_to_product_id()`; every build writes a fingerprint file next to the index (`engine/retrieval/fingerprint.py`).

# **7\. decision/**

## **7.1 decision.py (DecisionEngine)**

**Public API**: `DecisionEngine.decide(retrieval_result: RetrievalResult) -> DecisionResult`

Also exposes `evaluate_thresholds(similarity: float, detection_confidence: float) -> tuple[str, float]` as a pure, side-effect-free method — the single formula for accept/uncertain/reject, reused (not duplicated) by `Reranker`.

Sets `DecisionResult.needs_plugin` and `trigger_reasons` (subset of `{"uncertain", "ambiguous", "force"}`) and `forced_plugins` (union of the catalog `force_evidence` of every candidate in the Top-K, not just the winner; queried from `CatalogRepository` at use time).

**Forbidden**: no neural weight loading, no calls to Detector/Retriever/PluginManager/Reranker.

## **7.2 reranker.py (Reranker)**

Runs only when `DecisionResult.needs_plugin` is True, after `PluginManager`. **Public API**:

```Python

Reranker.rerank(retrieval_result: RetrievalResult, plugin_result: PluginResult) -> DecisionResult
```
Produces the FINAL `DecisionResult`. Re-scores every Top-K candidate (not just the original winner) using:

* Exact barcode match against `product["barcode"]`.  
* OCR text matched against the SKU's declared `ocr_keywords` only — never against the product name; no keywords means OCR score 0 (multi-orientation: evaluates every OCR orientation candidate independently per retrieval candidate, keeps the strongest). An exact keyword match gets at least `rerank.ocr.keyword_confidence_floor` (0.5; 0 = off) as its OCR confidence.  
* Color match, by `plugins.color.mode`:  
  * `"signature"` (default): the crop's colour signature is compared with each Top-K SKU's gallery signatures (`data/cache/color_signatures.npz`); only the best SKU gets evidence, and only if it beats the runner-up by more than `signature_min_margin` (strength reaches 1 at `signature_min_margin + signature_margin_scale`). The favoured SKU must declare a `color_code` in the catalog (colour is opt-in per SKU); if any candidate has no signature, no candidate gets colour evidence.  
  * `"roi"`: CIEDE2000 distance (Lab space) between the query colour and the `color_reference` (RGB, converted to OpenCV Lab inside the Reranker, cached by catalog `version()`) of the SKU's declared `color_code`, gated by both an absolute distance threshold and a margin-over-second-best requirement.  
* **Retrieval-consensus protection**: scales how much any plugin may influence the outcome by how strongly the Top-K already agrees with itself (`rerank.retrieval_protection`); a switch away from the original Top-1 is reverted if the winning margin is below `min_switch_margin`.  
* **Confusable-pair guard**: for pairs declared through catalog `confusable_with`, downgrades an otherwise-accepted decision back to `uncertain` unless at least `confusable_min_agreeing_plugins` independent plugins provided positive matching evidence. With `rerank.confusable_uncertain_without_evidence: true`, a winner flagged `confirm_if_unsure` in the catalog is also set to `uncertain` ("cần xác nhận" in the POS) when its confusable partner is among the candidates and no OCR, colour or barcode evidence tells them apart.

Reuses `DecisionEngine.evaluate_thresholds()` only — never re-invokes `decide()`. `DecisionEngine` itself never calls `PluginManager` or `Reranker`; only `InventoryPipeline` sequences `Decide -> Plugins -> Rerank`.

# **8\. plugins/**

## **8.1 manager.py (PluginManager)**

**Public API**: `PluginManager.run_plugins(crop: CropImage, decision: DecisionResult) -> PluginResult`

Selection policy: if `trigger_reasons` includes `"uncertain"` or `"ambiguous"`, every enabled plugin runs; if `trigger_reasons` is exactly `{"force"}`, only the plugins listed in `decision.forced_plugins` run. A plugin's own `enabled` flag always gates execution regardless of trigger reason.

## **8.2 ocr.py**

EasyOCR-based. Reads `crop.raw_image_array` only. Applies adaptive upscaling + CLAHE contrast enhancement, then evaluates every configured rotation angle (`plugins.ocr.rotation_angles`) independently, scoring each orientation by an information-content formula (favors longer, higher-quality, alphanumeric fragments over noise). Returns the best orientation, plus a second candidate when the top two orientations are ambiguous — both exposed to `Reranker` for independent per-candidate matching.

## **8.3 color.py**

Reads `crop.raw_image_array` only. Always computes the ROI dominant colour below; with `plugins.color.mode: "signature"` (default) it also returns the colour signature of the whole crop (`engine/core/color_signature.py`: centre-weighted (a\*, b\*) histogram of coloured pixels, no ROI, no reference colours), with confidence 0 when fewer than `min_colored_fraction` of the pixels have colour (e.g. a white box). The per-SKU gallery signatures are built offline by `scripts/build_color_signatures.py` (re-run after any gallery change).

ROI path (used by the `"roi"` mode): detects the product's own rectangular "powder pan" ROI via classical CV (Canny + contour scoring across five weighted criteria — rectangularity, centering, lower-position bias, inner margin, area), with a three-tier fallback (contour → center-crop → lower-center) if no candidate qualifies. Converts to Lab, removes highlight/glare pixels, runs K-Means (L-channel down-weighted, pixel counts center-weighted), and selects a chroma-aware dominant color. **Explicitly does not** read the catalog, identify color codes, or compare against reference colors — that is `Reranker`'s responsibility exclusively.

## **8.4 barcode.py**

Reads `crop.raw_image_array` only. Runs a pyzbar-based 9-stage cumulative adaptive decode pipeline (presence pre-check, raw decode, region detection, deskew, targeted upscale, CLAHE enhancement, binarization, denoise/sharpen, and multi-angle rotation fallback). Returns a confidence score derived from decode quality, symbology trust, and multi-code conflict penalties, along with normalized candidate strings (digits-only, UPC-A aligned to EAN-13/JAN). Explicitly does not read the catalog, resolve product identities, or evaluate candidate matches directly — that is `Reranker`'s responsibility exclusively.

Disabled by default (`plugins.barcode.enabled: false`, 2026-10-08): F1 was unchanged on both test sets with it off (a barcode was read 3 times out of 77; 28 of 33 SKUs have no barcode in the catalog) and each image is 1.0–1.5 s faster. Re-enable once real barcodes are entered.

# **9\. pipeline/pipeline.py (InventoryPipeline)**

**Public API**:

```Python

InventoryPipeline.run(image_data: ImageData, similarity_threshold: float | None = None, min_confidence_accept: float | None = None) -> InventoryResult

InventoryPipeline.run_with_trace(image_data: ImageData) -> tuple[InventoryResult, PipelineTrace]
```
Both execute the identical stage sequence:

```text
Detect (+ redundant-box suppression) -> Overlap -> Refine (if flagged) -> Crop -> [per crop: Retrieve -> Decide -> Plugins (if needed) -> Rerank (if plugins ran)] -> drop nested same-SKU items -> InventoryResult
```

`run()` skips trace bookkeeping for hot-path performance; VAL exclusively uses `run_with_trace()` so validation always measures the real production pipeline.

`InventoryPipeline.reload_catalog() -> str` re-reads the catalog (after an admin edit) without reloading any model and returns the new catalog `version()`.

**Per-call threshold overrides.** `similarity_threshold` and `min_confidence_accept` (both optional, `None` = configured default) override the matching `decision.*` values for one call only. Because `DecisionEngine`/`Reranker` hold no model weights, a temporary pair is built from an overridden config copy; pairs are cached in a small LRU (8 entries) keyed by the two override values, so a caller that passes the same thresholds on every request does not rebuild them. The cache is not thread-safe: call `run()` from one thread (the web backend does).

**`InventoryResult.has_overlap`** is `OverlapResult.needs_refinement` for the processed image. It is true only when the number of overlapping pairs reaches `refinement.trigger.min_overlapping_pairs`, so light overlap does not set it. Consumers (e.g. the POS UI) use it to suggest re-capturing; it does not change the result items. Both `run()` and `run_with_trace()` set it.

**`InventoryResult.detected_count`** is the number of regions the detector found, before the decision step. Items whose decision is `rejected` are dropped from `items`, so `detected_count - len(items)` is how many detected objects could not be recognised; the POS UI shows this so the cashier knows something was seen but not identified (e.g. a product missing from the gallery, or a blurry crop). `rejected_bboxes` (added later) lists the source-image boxes of regions whose final decision was `rejected`, so a UI can show where unrecognised objects are; `items` is unchanged.


**Forbidden**: no concrete model implementations, no file I/O, no UI.

# **10\. pipeline/build.py (BuildPipeline, offline)**

Orchestrates `sync_gallery` (catalog source `sqlite`: new gallery folders → new SKUs with `needs_naming`, image counts updated; nothing is ever deleted) and `GalleryIndexBuilder` (→ FAISS index + gallery metadata + fingerprint), rebuilding the index **only when the fingerprint changed** (`retrieval.build_gallery_index`). Runtime `Retriever` never rebuilds either. Independent from `InventoryPipeline`. Catalog details: `docs/04_DATA_AND_CATALOG.md`.

# **11\. storage/results.py (StorageManager)**

**Public API**: `save_json`, `save_csv`, `save_annotated_image`, `save_all`. No AI logic; never invoked by `InventoryPipeline` directly (callers are `InferenceRunner`/`ValidationRunner`).

# **12\. inference/infer.py (InferenceRunner)**

**Public API**:

```Python

InferenceRunner.run_single(image_path: str, similarity_threshold: float | None = None, min_confidence_accept: float | None = None, persist: bool = True) -> InventoryResult

InferenceRunner.run_batch(image_dir: str, similarity_threshold: float | None = None, min_confidence_accept: float | None = None) -> list[InventoryResult]
```
The optional threshold overrides are cheap: `DecisionEngine`/ `Reranker` hold no model weights, so building (and caching, see §9) a temporary overridden pair does not violate "never reload model weights per query."

`persist=False` skips `StorageManager.save_all`, so nothing is written. Callers that keep their own records (the web backend) use it; otherwise every call would overwrite the shared output files. The default (`True`) keeps the original behaviour. `run_batch` always persists.

`InferenceRunner.pipeline` exposes the loaded `InventoryPipeline` so a caller can reuse it (see §13.1) instead of loading the models twice; `InferenceRunner.reload_catalog()` forwards to `InventoryPipeline.reload_catalog()`.

# **13\. validation/**

## **13.1 validate.py (ValidationRunner)**

`ValidationRunner(config, pipeline=None)`: when `pipeline` is given, that already-loaded `InventoryPipeline` is reused (the web backend does this — a 4 GB GPU cannot hold two pipelines); otherwise a new one is built.

Loads COCO ground truth (`category_id` == numeric `product_id`), calls `InventoryPipeline.run_with_trace()` per benchmark image, forwards everything to `Evaluator`. Writes `report.json/csv`, `records.csv` (flat per-crop table, includes one row per fully-missed GT object), `summary.txt` (with full per-stage latency breakdown), color-coded annotated images (green=correct, red=wrong, dashed orange=missed), and a per-stage metrics bar chart.

## **13.2 evaluator.py (Evaluator)**

Computes one shared detection↔ground-truth match per image, reused by all 9 stages (Detection, Cropping, Overlap, Segmentation, Retrieval, Decision, Plugins, Fusion, End-to-End) so no stage can disagree with another about which crop corresponds to which ground-truth object. Each stage gathers raw statistics only; all formulas are delegated to `metrics.py`. A stage disabled via `validation.stages` reports the literal string `"skipped_by_config"`.

## **13.3 metrics.py**

Standalone registry of pure metric formulas (`precision`, `recall`, `f1`, `mrr`, `mean_rank`, `confusion_matrix`, `trigger_rate`, `correction_rate`, ...). Adding a new metric requires writing one function and registering it here — no changes to `evaluator.py`.

# **14\. ui/app.py (removed)**

The Tkinter desktop UI is no longer part of the engine (last present in git commit `f30710d`, `src/ui/`). The admin pages of the web POS replace it.

# **15\. Web POS (`backend/app/`, `frontend/`) — outside the engine**

Not a pipeline module. The engine described in this document now lives in `backend/engine/` (formerly `src/`). `backend/app/` (FastAPI + Postgres) runs the pipeline only through `RecognizerPort` → `LocalRecognizer` → `InferenceRunner`/`ValidationRunner`; the engine never imports `app`. When the web runs, the pipeline reads its catalog from Postgres (`catalog.source: database`). Rules: `03_DEVELOPMENT_RULES.md` §19. Architecture: `docs/system-architecture.md`. Usage: `docs/WEB.md`.

# **Dependency Rules Summary**

**Allowed flow**:

```text
UI/Runner -> InventoryPipeline -> [Detector, OverlapResolver, Refiner, Cropper, Retriever, DecisionEngine, PluginManager, Reranker] -> StorageManager
```

**Forbidden imports**:

* Detector → Retriever (either direction)  
* Plugin → Detector / Retriever  
* UI → Detector / Retriever / any concrete backend  
* StorageManager → InventoryPipeline  
* Reranker → DecisionEngine.decide() (may only call `evaluate_thresholds`)  
* DecisionEngine → PluginManager / Reranker  
* Any pipeline module → a concrete backend class (must go through its dispatcher: `Detector`, `Retriever`, or `Refiner`)