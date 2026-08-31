# Stocktaking AI

> AI-powered retail product inventory counting system — detects, segments, retrieves, and identifies individual products from shelf photos, fusing visual retrieval with OCR/Color/Barcode evidence to resolve visually near-identical product variants.

---

## 📑 Table of Contents
- [1. Overview](#1-overview)
- [2. System Pipeline](#2-system-pipeline)
- [3. Features](#3-features)
- [4. Project Structure](#4-project-structure)
- [5. Requirements](#5-requirements)
- [6. Installation](#6-installation)
- [7. Dataset](#7-dataset)
- [8. Configuration](#8-configuration)
- [9. Running the Project](#9-running-the-project)
- [10. Input & Output](#10-input--output)
- [11. Results](#11-results)
- [12. Experiments](#12-experiments)
- [13. Troubleshooting](#13-troubleshooting)
- [14. Limitations](#14-limitations)
- [15. Future Work](#15-future-work)
- [16. Citation](#16-citation)
- [17. License](#17-license)

---

## 1. Overview

Stocktaking AI automates retail shelf inventory counting from a single photograph. Given an image containing one or more products, the system:

1. Localizes every individual product on the shelf (object detection).
2. Refines bounding boxes when products visually overlap (segmentation).
3. Identifies *which specific product* each box contains via visual similarity search against a known product gallery.
4. Resolves ambiguous or visually near-identical products (e.g. two color variants of the same item, or two boxes differing by a single printed character) using secondary evidence — OCR text, dominant color, and barcode — fused through a dedicated reranking stage.
5. Produces a structured inventory count: total items, per-product quantity breakdown, and a full audit trail of every decision made.

The project separates *localization* from *identification* by design: detection is class-agnostic (it only answers "is there a product here?"), while product identity is resolved entirely downstream by retrieval + evidence fusion. This keeps each stage independently testable, swappable, and debuggable — which has proven essential, since most of this project's development effort has gone into diagnosing *why* identification fails on specific product pairs rather than into the detection stage itself (see [§12 Experiments](#12-experiments)).

This is an active research/development project. While the core fusion engine now achieves >90% End-to-End F1, some components (Barcode plugin, SAM2 refinement) are currently underperforming or configured off pending further tuning — see [§13 Troubleshooting](#13-troubleshooting) and [§14 Limitations](#14-limitations) for an honest account of current state.

---

## 2. System Pipeline

```text
Image
  │
  ▼
① DETECTION            Detector (RF-DETR / mock_contour backend)
  │                    — class-agnostic bounding boxes only
  ▼
② OVERLAP ANALYSIS      OverlapResolver
  │                    — flags suspicious box groups; NEVER removes a detection
  ▼
③ SEGMENTATION           Refiner (SAM2 / mock_refiner / none)
  │  [only when Overlap flags a group]
  ▼
④ CROPPING               Cropper
  │                      — produces TWO crops per detection:
  │                        image_array (resized, for Retriever)
  │                        raw_image_array (original resolution, for Plugins)
  ▼
⑤ RETRIEVAL                Retriever (SigLIP2 / mock_visual_embedding + FAISS)
  │                        — Top-K nearest gallery products by cosine similarity
  ▼
⑥ DECISION                   DecisionEngine
  │                          — accept / uncertain / reject by similarity threshold
  │                          — flags needs_plugin for 3 independent reasons:
  │                            uncertain | ambiguous | force (per-product rules)
  ▼
⑦ PLUGINS [optional]         PluginManager → OCR / Color / Barcode
  │                         — only runs when Decision requests evidence
  ▼
⑧ RERANKER                   Reranker
  │                         — fuses evidence across the FULL Top-K
  │                         — retrieval-consensus protection (weak evidence
  │                           cannot override a strongly-agreed-upon Top-1)
  │                         — confusable-pair guard for known hard cases
  ▼
InventoryResult → StorageManager (JSON / CSV / annotated image)
```

Two entry points exist for the same pipeline: `InventoryPipeline.run()` (production inference) and `InventoryPipeline.run_with_trace()` (returns every intermediate stage result, used exclusively by the Validation system — see [§9](#9-running-the-project)). Both execute the identical pipeline; `run_with_trace()` never runs a simplified/parallel version, so validation metrics always reflect real inference behavior.

---

## 3. Features

- **Pluggable backend architecture** — Detection, Retrieval, and Refinement are each a thin dispatcher over swappable backend implementations, selected purely by config:
  - Detection: `mock_contour` (classical CV, no weights) / `rf_detr` (real RF-DETR)
  - Retrieval: `mock_visual_embedding` (HSV histogram, no weights) / `siglip2` (real SigLIP2, mean-pooled)
  - Refinement: `none` / `mock_refiner` / `sam2`
- **Dual-resolution cropping** — every detection produces both a retrieval-optimized resized crop and an original-resolution raw crop, so OCR/Barcode plugins never lose small print detail to a forced downsize.
- **Three-reason plugin trigger policy** — `uncertain` (borderline similarity), `ambiguous` (Top-N candidates too close to separate), `force` (per-product rules mandating specific evidence, e.g. always confirm certain products via color).
- **Evidence-driven Reranker** — re-scores the entire Top-K (not just the retrieval winner) using:
  - Barcode exact-match against catalog
  - OCR text matched against catalog tokens (multi-orientation: tries 0°/90°/180°/270°, picks the best-scoring reading; falls back to a second orientation when ambiguous)
  - Color matched via CIEDE2000 distance in Lab space against hardcoded per-variant color references
  - **Retrieval-consensus protection**: weak plugin evidence is prevented from overriding a Top-1 the retrieval index already agrees on strongly, scaled by how strong that consensus is
  - **Confusable-pair guard**: for explicitly configured hard pairs (e.g. two products differing by one printed character), requires a minimum number of independently agreeing plugins before accepting
- **9-stage staged validation (VAL)** — Detection, Cropping, Overlap, Segmentation, Retrieval, Decision, Plugins, Fusion, End-to-End, all computed from a *single* real pipeline execution per benchmark image (no simplified evaluation path). Metric formulas live in a standalone registry (`metrics.py`) so new metrics require no changes to the evaluator itself. Every stage and every metric is individually toggleable via config.
- **Rich VAL artifacts** — per-crop flat `records.csv` (pandas-ready, includes one row per fully-missed ground-truth object), color-coded annotated benchmark images (green=correct, red=wrong, dashed orange=missed), a per-stage metrics bar chart, and a full per-stage latency breakdown.
- **Desktop UI (Tkinter)** — Inference tab with a large Total-Items-Counted panel and per-product quantity breakdown (this is fundamentally a counting system); zoomable annotated-result and matched-gallery-product viewers; runtime similarity-threshold override without reloading any model. Validation tab surfaces the full 9-stage report plus chart/annotated-image viewers.

---

## 4. Project Structure

```text
stocktaking_ai/
├── README.md
├── requirements.txt
├── run.py                       # CLI entry point (build -> infer/validate/ui)
├── configs/
│   └── config.yaml              # single source of truth for all runtime parameters
├── data/
│   ├── gallery/                 # raw product photos, one folder per product
│   ├── metadata/
│   │   ├── products.json        # build output: full product catalog
│   │   ├── product_ids.json     # build output: product_id <-> gallery folder mapping
│   │   └── product_colors.json  # HAND-AUTHORED: {code: {name, rgb, hex}} color references
│   ├── benchmark/
│   │   ├── images/
│   │   └── _annotations.coco.json
│   ├── query/
│   ├── outputs/                 # result.*, report.*, records.csv, validation_images/, charts
│   └── cache/                   # gallery FAISS index + logs
├── weights/                     # detector / retriever / refinement model weights
├── scripts/
│   └── generate_sample_data.py
├── src/
│   ├── core/                    # config loader, logger, utils
│   ├── models/                  # all Data Transfer Objects (dataclasses)
│   ├── catalog/                 # MetadataBuilder (offline product_id assignment)
│   ├── detection/
│   │   ├── detector.py          # dispatcher
│   │   ├── backends/            # mock_contour, rf_detr
│   │   └── cropper.py           # dual-resolution cropping
│   ├── pipeline/
│   │   ├── pipeline.py          # InventoryPipeline orchestrator
│   │   ├── overlap.py           # OverlapResolver + find_suspicious_pairs()
│   │   └── build.py             # offline build pipeline (metadata + gallery index)
│   ├── segmentation/
│   │   ├── refiner.py           # dispatcher
│   │   └── backends/            # none, mock_refiner, sam2
│   ├── retrieval/
│   │   ├── retriever.py         # dispatcher, runtime-only (loads pre-built index)
│   │   ├── backends/            # mock_visual_embedding, siglip2
│   │   └── gallery_builder.py   # offline FAISS index builder
│   ├── decision/
│   │   ├── decision.py          # DecisionEngine (thresholds + trigger policy)
│   │   └── reranker.py          # evidence fusion, retrieval protection, confusable pairs
│   ├── plugins/
│   │   ├── manager.py
│   │   ├── ocr.py               # EasyOCR, multi-orientation, CLAHE, adaptive upscale
│   │   ├── color.py             # ROI-aware K-Means in Lab space, highlight removal
│   │   └── barcode.py           # pyzbar (currently disabled in config)
│   ├── storage/                 # results.py (JSON/CSV/annotated image export)
│   ├── inference/                # infer.py (InferenceRunner)
│   ├── validation/
│   │   ├── validate.py          # ValidationRunner (COCO loading, report writing)
│   │   ├── evaluator.py         # 9-stage staged metric gathering
│   │   └── metrics.py           # metric formula registry
│   └── ui/                      # app.py (Tkinter desktop UI)
└── tests/                       # isolated unit + integration tests (pytest)
```

---

## 5. Requirements

- Python >= 3.11
- Core: `numpy`, `opencv-python-headless`, `PyYAML`, `pydantic`, `Pillow`, `matplotlib`, `faiss-cpu`
- Real detection backend: `torch`, `torchvision`, `rfdetr`, `supervision`
- Real retrieval backend: `torch`, `transformers`
- Real refinement backend: `torch`, `sam2`
- OCR plugin: `easyocr`
- Barcode plugin: `pyzbar` (requires system `libzbar0`)
- Desktop UI: `tkinter` (ships with most Python distributions; `sudo apt-get install python3-tk` on Debian/Ubuntu if missing)
- CUDA-capable GPU strongly recommended (>= 8GB VRAM) — the default `config.yaml` runs RF-DETR, SAM2, and SigLIP2 all on `cuda`; CPU-only inference works but is slow.

---

## 6. Installation

```bash
git clone <repository-url>
cd stocktaking_ai
pip install -r requirements.txt --break-system-packages

# System dependency for the Barcode plugin (if enabling it):
sudo apt-get install libzbar0
```

Model weights are **not** bundled in the repository and must be placed manually:

| Backend | Expected path |
|---|---|
| RF-DETR fine-tuned checkpoint | `weights/detector/checkpoint_best_ema.pth` |
| SAM2 checkpoint | `weights/refinement/sam2/sam2.1_hiera_small.pt` |
| SigLIP2 | downloaded automatically from Hugging Face Hub on first run (`google/siglip2-base-patch16-224`) |

---

## 7. Dataset

**Gallery** (`data/gallery/`): one folder per product, folder name = product display name (matched to `configs/config.yaml -> catalog.id_mapping` to assign a stable numeric `product_id`; unmapped folders receive new sequential IDs automatically). Any number of reference photos per folder.

**Color references** (`data/metadata/product_colors.json`): **currently hand-authored**, not generated by the build pipeline. Maps a color/variant code (as it appears in the product name, e.g. `PK300`) to an RGB reference value:

```json
{
  "PK300": {
    "name": "PK300",
    "rgb": [109, 63, 62],
    "hex": "#6D3F3E"
  }
}
```

**Benchmark** (`data/benchmark/`): COCO-format object detection annotations.

```text
data/benchmark/
├── images/
│   └── *.jpg
└── _annotations.coco.json
```

Ground-truth `category_id` must equal the numeric `product_id` used everywhere else in the system (from `catalog.id_mapping` / `product_ids.json`) — no separate ID system is created for validation.

---

## 8. Configuration

All runtime parameters live in `configs/config.yaml`; no Python source file hard-codes a threshold, path, or model name. Key sections:

| Section | Controls |
|---|---|
| `catalog` | Product ID assignment (`id_mapping`), metadata build toggle |
| `detection` | Backend choice, confidence/area/aspect filters, RF-DETR variant + inference options |
| `refinement` | Segmentation backend, geometry-based trigger thresholds, SAM2 params, output sanity bounds |
| `cropping` | Padding, resized target size (for Retriever) |
| `retrieval` | Backend choice, embedding dimension, Top-K, FAISS/catalog paths |
| `decision` | Accept/uncertain/reject thresholds, ambiguous-band detection params |
| `plugins` | Per-plugin enable + full tuning surface (OCR: rotation/CLAHE/upscale; Color: ROI detection + K-Means + highlight removal), `force_rules` (per-product mandatory plugins) |
| `rerank` | Evidence weights per plugin, retrieval-consensus protection curve, confusable-pair guard, color reference path + ΔE thresholds |
| `storage` | Export toggles and annotated-image styling |
| `validation` | IoU threshold, per-stage enable switches, per-stage metric selection, artifact export toggles |

To add a new evidence-fusion weight or VAL metric, edit `config.yaml` only — no code changes are required for parameter tuning.

---

## 9. Running the Project

The CLI (`run.py`) runs the offline build pipeline (metadata + gallery index) before every command unless `--skip-build` is passed.

```bash
# Single-image inference
python run.py --mode infer --image data/query/test_shelf.jpg

# Batch inference over a directory
python run.py --mode infer --image-dir data/query/

# Validation against a COCO benchmark
python run.py --mode validate --benchmark-dir data/benchmark/

# Desktop UI
python run.py --mode ui
```

Programmatic use:

```python
from src.core.config import load_config
from src.pipeline.pipeline import InventoryPipeline

config = load_config()
pipeline = InventoryPipeline(config)
result = pipeline.run(image_data)                       # production inference
result, trace = pipeline.run_with_trace(image_data)      # + full stage trace, for VAL/debugging
```

---

## 10. Input & Output

| Mode | Input | Output (`data/outputs/`) |
|---|---|---|
| Inference | Single image or directory | `result.json` (full `InventoryResult`), `result.csv` (flat item table), `result.jpg` (annotated) |
| Validation | COCO benchmark directory | `report.json` / `report.csv` (9-stage metrics + per-image/per-product breakdown), `records.csv` (flat per-crop table), `summary.txt` (incl. per-stage latency), `validation_images/*.jpg` (color-coded), `validation_summary_chart.png` |
| UI (Inference tab) | Selected image | Total item count, per-product quantity table, annotated preview |
| UI (Validation tab) | Selected benchmark dir | Full 9-stage report text, chart viewer, annotated-image viewer |

---

## 11. Results

Latest full validation run (18-product catalog, 31 images, 293 ground-truth instances):

| Stage | Key metric | Value |
|---|---|---|
| Detection | F1 (class-agnostic, bbox IoU) | **0.950** |
| Cropping | valid_rate | **1.000** |
| Overlap | F1 | **0.920** |
| Segmentation (SAM2) | mean IoU improvement | **+0.011** (marginal) |
| Retrieval | Top-1 accuracy | **0.739** |
| Retrieval | Top-K accuracy (K=5) | **0.990** |
| Decision | precision (pre-evidence) | **0.739** |
| Fusion | accuracy improvement | **+0.224** |
| End-to-End | F1 | **0.901** |

**The Evidence Fusion architecture is the standout success of this pipeline.** By intelligently combining OCR and Color signals, the Reranker successfully corrected 59 ambiguous visual matches while generating **zero new errors** (0 regressions). This pushes the post-fusion classification accuracy to 93.9% and the End-to-End F1 score to an excellent 0.901.

---

## 12. Experiments

A condensed timeline of root-cause debugging performed on this system, kept here because each fix materially changed downstream numbers and the reasoning is easy to lose otherwise:

1. **`products.json` identity bug** — `product_id` field was accidentally populated with the gallery folder name instead of the stable internal numeric ID in one code path, silently breaking name resolution. Fixed; `product_id` is now guaranteed to be the numeric ID everywhere in the system.
2. **SigLIP2 patch-embedding bug (major)** — `get_image_features()` was returning unpooled per-patch features `(1, 196, 768)` instead of a pooled global embedding `(1, 768)`. The embedding pipeline was silently comparing a single 16×16 image patch, not the whole product. Fixed via mean-pooling across the patch dimension. **Retrieval Top-1 accuracy went from ~3% to ~55%** — this was the single largest fix in the project.
3. **OCR resize-before-read bug** — crops were resized to a fixed target size (shared with the Retriever) before OCR, destroying small printed text. Fixed by giving every crop a second, original-resolution `raw_image_array` used exclusively by OCR/Color/Barcode plugins. **OCR success rate went from ~54% to ~98%.**
4. **OCR rotation sensitivity** — real shelf photos have products rotated freely; EasyOCR's own text-line detection cannot recover from large arbitrary rotations. Addressed with a multi-orientation pass (0°/90°/180°/270°) plus CLAHE contrast enhancement and adaptive upscaling for small crops; ambiguous orientations retain a second candidate for the Reranker to evaluate independently.
5. **OCR/barcode cross-contamination** — barcode stripes were frequently misread by EasyOCR as garbage alphanumeric text (`'0 1 1 I li'`), diluting fuzzy-match quality against catalog names. Addressed via catalog-token extraction (exact substring/token matching preferred over whole-string fuzzy matching) rather than image-level barcode masking.
6. **Naive Fusion vs. Guarded Fusion** — an early version of the Reranker corrected roughly as many wrong decisions as it newly broke (net improvement ≈ 0). This motivated the introduction of **retrieval-consensus protection** (scaling plugin authority by how strongly the retrieval index already agrees with itself) and the **confusable-pair guard**. With these guards tuned, the Fusion stage became flawless: it now correctly flips ~22% of predictions with **zero newly incorrect cases**, driving the entire pipeline to >0.90 F1.

---

## 13. Troubleshooting

- **Barcode plugin: 0% decode success rate, currently disabled (`plugins.barcode.enabled: false`).** Root cause not yet fully isolated between (a) crop quality/resolution at the barcode location, (b) barcode not fully contained within the detected bounding box, and (c) `products.json.barcode` being empty for most catalog entries regardless of decode success. Re-enable only after investigating decode failures directly.
- **SAM2 refinement provides marginal benefit** (`iou_improvement ≈ +0.01`) and degrades roughly as many boxes as it improves (`degraded_count` close to half of `refinement_success_count`). Consider tightening `refinement.output.min_mask_coverage_ratio` / `max_bbox_expansion_ratio`, or leaving `refinement.enabled: false` until the trigger/output policy is retuned.
- **Product pairs 7 vs. 8:** While the Fusion engine has resolved the vast majority of visual ambiguities (e.g., 5 vs 6 is now nearly perfect), products 7 and 8 (which differ by a single printed character) still account for the majority of the remaining End-to-End false classifications. Ensure their `force_rules` configurations are strict.
- If `Retriever` raises `FileNotFoundError` on startup, the gallery index has not been built yet — run without `--skip-build`, or check `catalog.build_metadata` / `retrieval.build_gallery_index` are not both disabled in config.

---

## 14. Limitations

- While End-to-End F1 is high (0.90), the remaining ~6% identification error is heavily concentrated in a few specific product variants (like 7 vs 8). In a strict production environment, items flagged as `ambiguous` between these specific IDs still warrant human review.
- `product_colors.json` is entirely hand-authored — it does not scale to large catalogs and is not regenerated by the offline build pipeline.
- `products.json.barcode` is empty for essentially every catalog entry, so barcode evidence cannot currently contribute even when a barcode is successfully decoded.
- The Overlap-stage validation metric derives "ground-truth overlap pairs" from geometric GT box relationships rather than separately annotated overlap labels — a reasonable approximation, but not independently verified ground truth.
- OCR does not read Japanese text (`language: [en]` only); packaging text that is exclusively Kanji/Katakana contributes no evidence.
- CPU-only inference is supported but not performance-tuned; the default configuration assumes a CUDA GPU.

---

## 15. Future Work

- Auto-generate `product_colors.json` from gallery images during the offline build step (candidate approach: run the Color plugin's ROI+K-Means pipeline over each product's reference photos), reducing reliance on hand authoring.
- Populate real barcode values in `products.json` (from supplier data or a one-time decode-and-confirm pass) so barcode evidence can actually be used.
- Diagnose and fix Barcode plugin decode failures directly (crop resolution vs. bbox coverage vs. detection-stage barcode localization).
- Retune or replace SAM2 refinement trigger/output policy so it net-improves rather than roughly breaking even.
- Extend VAL Stage 2/3 findings into automated regression thresholds (fail CI/build if a metric regresses beyond a configured tolerance).
- Add Japanese OCR support for packaging text that is currently unreadable.

---

## 16. Citation

Not applicable — internal research/development project, no associated publication.

---

## 17. License

Not yet assigned. Add a license file before external distribution.