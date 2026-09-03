# STOCKTAKING AI
## MODULE SPECIFICATION

Version: 0.1.0 (extended)

---

# Purpose

Defines the formal boundary, responsibility, data contract, public API,
and forbidden behaviors for every module. Implementation details belong
to the source code itself.

---

# Overall Module Relationship Flow

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
    Detection  OverlapResolver  Refiner    Retrieval
         │          │           │             │
         └──────────┴─────┬─────┴─────────────┘
                          ▼
                    DecisionEngine
                          │
                          ▼
                    PluginManager (on-demand)
                          │
                          ▼
                       Reranker
                          │
                          ▼
                    StorageManager
```

Only `InventoryPipeline` coordinates data flow between AI modules.

---

# 1. core/

**Files**: `config.py`, `logger.py`, `utils.py`

**Responsibilities**: load/validate `configs/config.yaml` into typed
Pydantic models; structured logging; generic filesystem/image helpers.

**Public APIs**: `load_config() -> AppConfig`, `setup_logger(name) -> Logger`,
`get_logger(name) -> Logger`

**Forbidden**: no AI/ML inference logic, no UI logic.

---

# 2. models/

**Files**: `models.py`

**Key DTOs**: `BoundingBox`, `ImageData`, `Detection`/`DetectionResult`,
`OverlapPair`/`OverlapGroup`/`OverlapResult`, `RefinedBox`/`RefinementResult`,
`CropImage` (dual-resolution — see below), `RetrievalCandidate`/`RetrievalResult`,
`DecisionResult`, `PluginResult`, `InventoryItem`/`InventoryResult`,
`CropTrace`/`PipelineTrace`.

**`CropImage` contract**:
```python
image_array: np.ndarray       # resized (cropping.target_size) — Retriever only
raw_image_array: np.ndarray   # original resolution, clip+pad only — Plugins only
```
Both fields are always populated by `Cropper`; no other module may
resize either array after the fact.

**`RefinementResult` contract**: independent from `DetectionResult` by
design — `Detector`'s output contract must never be overwritten by the
optional segmentation stage. `Cropper` is the only module that reads
both together.

**`PipelineTrace`**: diagnostics-only, produced solely by
`InventoryPipeline.run_with_trace()`; never produced by `run()`.

**Forbidden**: no business logic, no AI/ML library imports.

---

# 3. detection/

## 3.1 detector.py

Thin dispatcher. **Public API**: `Detector.detect(image_data: ImageData) -> DetectionResult`

**Forbidden**: classification, retrieval, cropping, plugin execution,
file I/O.

## 3.2 backends/

`base.py` (abstract `DetectionBackend`), `mock_contour.py` (Canny +
contours, no weights), `rf_detr.py` (real RF-DETR neural detector).
Detection output is **class-agnostic by design** — product identity is
never resolved here (see Retrieval).

## 3.3 cropper.py

**Public API**:
```python
Cropper.crop(image_data: ImageData, detection_result: DetectionResult, refinement_result: RefinementResult) -> list[CropImage]
```

For each detection: uses `RefinedBox.refined_bbox` when present, not a
fallback, and `cropping.use_refined_bbox` is enabled; otherwise the
original `Detection.bbox`. Produces both `image_array` (resized) and
`raw_image_array` (original resolution) per crop.

**Forbidden**: detection, segmentation, or retrieval algorithms;
mutating `DetectionResult`.

---

# 4. pipeline/overlap.py (OverlapResolver)

Purely geometric. **Public API**:
```python
OverlapResolver.resolve(detection_result: DetectionResult) -> OverlapResult
find_suspicious_pairs(detections: list[Detection], iou_threshold: float, overlap_ratio_threshold: float) -> list[OverlapPair]
```

`find_suspicious_pairs` is a pure module-level function — the single
source of truth for "what counts as suspicious overlap," reused
identically by `OverlapResolver` at runtime and by VAL's Overlap-stage
evaluator, so the two can never silently disagree.

**Forbidden**: this is explicitly **not NMS** — never removes or mutates
any `Detection`. Never decides which segmentation backend to use.

---

# 5. segmentation/ (Refiner + backends)

Optional stage, invoked only when `OverlapResolver` sets
`needs_refinement=True`. **Public API**:
```python
Refiner.refine(image_array: np.ndarray, detection_result: DetectionResult, overlap_result: OverlapResult) -> RefinementResult
```

`backends/base.py` (abstract `SegmentationBackend`), `mock_refiner.py`
(passthrough fallback), `sam2.py` (real SAM2). The pipeline never
imports a concrete backend directly — only `Refiner`.

**Forbidden**: never mutates `DetectionResult`.

---

# 6. retrieval/

## 6.1 retriever.py

Runtime-only. **Public API**: `Retriever.retrieve(crop: CropImage) -> RetrievalResult`

Loads a *pre-built* FAISS index + catalog; never builds or rebuilds
either at runtime. `product_id` throughout this module (and everywhere
else in the system) is always the stable internal numeric ID as a
string — never a gallery folder name.

**Forbidden**: detection, cropping, OCR, barcode decoding, direct
Storage/UI interaction.

## 6.2 backends/

`base.py` (abstract `EmbeddingBackend`), `mock_visual_embedding.py`
(HSV histogram, no weights), `siglip2.py` (real SigLIP2; embedding is
mean-pooled across patch tokens before use — see
`03_DEVELOPMENT_RULES.md` Appendix, Experiment 2, for why this pooling
step is mandatory, not optional).

## 6.3 gallery_builder.py

Build-time only (invoked by `pipeline/build.py`). Never imported by
runtime `Retriever`.

---

# 7. decision/

## 7.1 decision.py (DecisionEngine)

**Public API**: `DecisionEngine.decide(retrieval_result: RetrievalResult) -> DecisionResult`

Also exposes `evaluate_thresholds(similarity: float, detection_confidence: float) -> tuple[str, float]`
as a pure, side-effect-free method — the single formula for
accept/uncertain/reject, reused (not duplicated) by `Reranker`.

Sets `DecisionResult.needs_plugin` and `trigger_reasons` (subset of
`{"uncertain", "ambiguous", "force"}`) and `forced_plugins` (resolved
against every candidate in the Top-K via `plugins.force_rules`, not just
the winner).

**Forbidden**: no neural weight loading, no calls to
Detector/Retriever/PluginManager/Reranker.

## 7.2 reranker.py (Reranker)

Runs only when `DecisionResult.needs_plugin` is True, after
`PluginManager`. **Public API**:
```python
Reranker.rerank(retrieval_result: RetrievalResult, plugin_result: PluginResult) -> DecisionResult
```

Produces the FINAL `DecisionResult`. Re-scores every Top-K candidate
(not just the original winner) using:
- Exact barcode match against `product["barcode"]`.
- OCR text matched against catalog-derived alphanumeric tokens
  (multi-orientation: evaluates every OCR orientation candidate
  independently per retrieval candidate, keeps the strongest).
- Color match via CIEDE2000 distance (Lab space) against
  `data/metadata/product_colors.json`, gated by both an absolute
  distance threshold and a margin-over-second-best requirement.
- **Retrieval-consensus protection**: scales how much any plugin may
  influence the outcome by how strongly the Top-K already agrees with
  itself (`rerank.retrieval_protection`); a switch away from the
  original Top-1 is reverted if the winning margin is below
  `min_switch_margin`.
- **Confusable-pair guard**: for pairs listed in
  `rerank.confusable_pairs`, downgrades an otherwise-accepted decision
  back to `uncertain` unless at least `confusable_min_agreeing_plugins`
  independent plugins provided positive matching evidence.

Reuses `DecisionEngine.evaluate_thresholds()` only — never re-invokes
`decide()`. `DecisionEngine` itself never calls `PluginManager` or
`Reranker`; only `InventoryPipeline` sequences
`Decide -> Plugins -> Rerank`.

---

# 8. plugins/

## 8.1 manager.py (PluginManager)

**Public API**: `PluginManager.run_plugins(crop: CropImage, decision: DecisionResult) -> PluginResult`

Selection policy: if `trigger_reasons` includes `"uncertain"` or
`"ambiguous"`, every enabled plugin runs; if `trigger_reasons` is
exactly `{"force"}`, only the plugins listed in `decision.forced_plugins`
run. A plugin's own `enabled` flag always gates execution regardless of
trigger reason.

## 8.2 ocr.py

EasyOCR-based. Reads `crop.raw_image_array` only. Applies adaptive
upscaling + CLAHE contrast enhancement, then evaluates every configured
rotation angle (`plugins.ocr.rotation_angles`) independently, scoring
each orientation by an information-content formula (favors longer,
higher-quality, alphanumeric fragments over noise). Returns the best
orientation, plus a second candidate when the top two orientations are
ambiguous — both exposed to `Reranker` for independent per-candidate
matching.

## 8.3 color.py

Reads `crop.raw_image_array` only. Detects the product's own
rectangular "powder pan" ROI via classical CV (Canny + contour
scoring across five weighted criteria — rectangularity, centering,
lower-position bias, inner margin, area), with a three-tier fallback
(contour → center-crop → lower-center) if no candidate qualifies.
Converts to Lab, removes highlight/glare pixels, runs K-Means (L-channel
down-weighted, pixel counts center-weighted), and selects a
chroma-aware dominant color. **Explicitly does not** read the catalog,
identify color codes, or compare against reference colors — that is
`Reranker`'s responsibility exclusively.

## 8.4 barcode.py

pyzbar-based, reads `crop.raw_image_array` only. 9-stage cumulative adaptive decode pipeline
(each stage attempted only if the previous one failed to decode anything):

```text
-1. Presence pre-check (edge density gate — cheap reject for barcode-free crops)
 0. Raw decode
 1. Barcode-region detection (gradient anisotropy, rotation-invariant)
    -> if no region found after step 0 fails, stop (very likely no barcode)
 2. Deskew (seeded from detected region angle, else a dedicated Hough pass)
 3. Upscale (cropped to detected region first, so resolution isn't spent on background)
 4. CLAHE contrast enhancement
 5. Adaptive threshold binarization
 6. Denoise
 7. Sharpen
 8. Fixed-angle rotation fallback (90/180/270) — last resort
```

Returns a `confidence` score (not `confidence_boost`) combining decode quality, symbology
trust, and a penalty when multiple decoded barcodes in the same crop disagree. Decoded values
and catalog values are normalized before matching (digits only; UPC-A aligned to EAN-13/JAN by
leading-zero prepend) — see `Reranker._barcode_match_strength`.

Currently disabled via `plugins.barcode.enabled: false` pending a validation run against this
rewritten pipeline.
---

# 9. pipeline/pipeline.py (InventoryPipeline)

**Public API**:
```python
InventoryPipeline.run(image_data: ImageData) -> InventoryResult
InventoryPipeline.run_with_trace(image_data: ImageData) -> tuple[InventoryResult, PipelineTrace]
```

Both execute the identical stage sequence:
```text
Detect -> Overlap -> Refine (if flagged) -> Crop -> [per crop: Retrieve -> Decide -> Plugins (if needed) -> Rerank (if plugins ran)] -> InventoryResult
```

`run()` skips trace bookkeeping for hot-path performance; VAL exclusively
uses `run_with_trace()` so validation always measures the real
production pipeline.

**Forbidden**: no concrete model implementations, no file I/O, no UI.

---

# 10. pipeline/build.py (BuildPipeline, offline)

Orchestrates `MetadataBuilder` (→ `products.json`, `product_ids.json`)
and `GalleryIndexBuilder` (→ FAISS index + gallery metadata). Runtime
`Retriever` never rebuilds either. Independent from `InventoryPipeline`.

---

# 11. storage/results.py (StorageManager)

**Public API**: `save_json`, `save_csv`, `save_annotated_image`,
`save_all`. No AI logic; never invoked by `InventoryPipeline` directly
(callers are `InferenceRunner`/`ValidationRunner`).

---

# 12. inference/infer.py (InferenceRunner)

**Public API**:
```python
InferenceRunner.run_single(image_path: str, similarity_threshold: float | None = None) -> InventoryResult
InferenceRunner.run_batch(image_dir: str, similarity_threshold: float | None = None) -> list[InventoryResult]
```

The optional `similarity_threshold` override is cheap: `DecisionEngine`/
`Reranker` hold no model weights, so building a temporary overridden pair
per call does not violate "never reload model weights per query."

---

# 13. validation/

## 13.1 validate.py (ValidationRunner)

Loads COCO ground truth (`category_id` == numeric `product_id`), calls
`InventoryPipeline.run_with_trace()` per benchmark image, forwards
everything to `Evaluator`. Writes `report.json/csv`, `records.csv`
(flat per-crop table, includes one row per fully-missed GT object),
`summary.txt` (with full per-stage latency breakdown), color-coded
annotated images (green=correct, red=wrong, dashed orange=missed), and
a per-stage metrics bar chart.

## 13.2 evaluator.py (Evaluator)

Computes one shared detection↔ground-truth match per image, reused by
all 9 stages (Detection, Cropping, Overlap, Segmentation, Retrieval,
Decision, Plugins, Fusion, End-to-End) so no stage can disagree with
another about which crop corresponds to which ground-truth object.
Each stage gathers raw statistics only; all formulas are delegated to
`metrics.py`. A stage disabled via `validation.stages` reports the
literal string `"skipped_by_config"`.

## 13.3 metrics.py

Standalone registry of pure metric formulas (`precision`, `recall`,
`f1`, `mrr`, `mean_rank`, `confusion_matrix`, `trigger_rate`,
`correction_rate`, ...). Adding a new metric requires writing one
function and registering it here — no changes to `evaluator.py`.

---

# 14. ui/app.py

**Forbidden**: never imports Detector/Retriever/any model class
directly; communicates only via `InferenceRunner`/`ValidationRunner`.

---

# Dependency Rules Summary

**Allowed flow**:
```text
UI/Runner -> InventoryPipeline -> [Detector, OverlapResolver, Refiner, Cropper, Retriever, DecisionEngine, PluginManager, Reranker] -> StorageManager
```

**Forbidden imports**:
- Detector → Retriever (either direction)
- Plugin → Detector / Retriever
- UI → Detector / Retriever / any concrete backend
- StorageManager → InventoryPipeline
- Reranker → DecisionEngine.decide() (may only call `evaluate_thresholds`)
- DecisionEngine → PluginManager / Reranker
- Any pipeline module → a concrete backend class (must go through its
  dispatcher: `Detector`, `Retriever`, or `Refiner`)