# STOCKTAKING AI
## PROJECT CONTEXT

Version: 0.1.0 (extended)

---

# 1. Project Overview

Stocktaking AI is a research-oriented AI Product Inventory System. It
automatically counts and identifies retail products from a single shelf
photograph.

Unlike a traditional single-stage object detector, this project strictly
separates **product localization** (where is a product?) from **product
identification** (which specific product is it?). Localization is
class-agnostic; identification is resolved entirely by a downstream
visual-retrieval + evidence-fusion pipeline. This separation was not
incidental — it is the direct result of a debugging process that
repeatedly found identification, not localization, to be the actual
bottleneck (see Section 10).

The complete inventory process consists of:

- Product Detection (class-agnostic)
- Overlap Analysis
- Segmentation Refinement (optional, geometry-triggered)
- Product Cropping (dual-resolution)
- Image Retrieval (Top-K visual similarity)
- Decision (accept/uncertain/reject + plugin trigger policy)
- Plugin Evidence Gathering (OCR / Color / Barcode, optional)
- Evidence Fusion / Reranking
- Inventory Result Generation

---

# 2. Project Objectives

- Build a complete, correctly-ordered end-to-end AI inventory pipeline.
- Keep every stage independently swappable via configuration (backend
  dispatcher pattern), never via code changes.
- Resolve visually near-identical product variants ("confusable pairs")
  using secondary evidence, without secondary evidence silently
  overriding a retrieval result the index already agrees on strongly.
- Provide a staged validation system (VAL) that measures the *actual
  production pipeline* — never a simplified parallel evaluation path —
  at the granularity of each individual stage, so regressions can be
  attributed to a specific stage rather than only observed end-to-end.
- Provide a desktop UI for demonstration and interactive validation
  review.

---

# 3. Core Design Philosophy

## 3.1 Pipeline-Centered Architecture

Every runtime workflow executes through exactly one entry point family:
`InventoryPipeline.run()` (production) or `InventoryPipeline.run_with_trace()`
(validation/debugging — identical pipeline, additionally returns every
intermediate stage's output). No module may bypass the pipeline, and no
module may call another pipeline stage's module directly except through
the pipeline orchestrator.

## 3.2 Single Responsibility, Strictly Enforced Across Stages

- **Detector**: locates products. Never classifies, never touches pixels
  beyond its own bounding-box output.
- **OverlapResolver**: flags geometrically suspicious detection groups.
  Never removes or mutates a detection (this is explicitly *not* NMS).
- **Refiner**: tightens a bounding box via segmentation, only for
  detections OverlapResolver flagged. Never invoked otherwise.
- **Cropper**: slices pixel regions. Produces two resolutions per
  detection (see Section 3.5) but performs no further processing.
- **Retriever**: generates an embedding and searches a *pre-built*
  gallery index. Never builds or rebuilds that index at runtime.
- **DecisionEngine**: applies threshold rules and determines *whether*
  and *why* secondary evidence is needed. Never gathers that evidence
  itself, never calls PluginManager.
- **PluginManager**: gathers evidence only when DecisionEngine requests
  it, and only the specific plugins actually required.
- **Reranker**: the only module permitted to change a decision after
  evidence has been gathered. Never re-invokes DecisionEngine's full
  decision logic — reuses only its pure threshold formula.
- **StorageManager / UI**: no AI logic; UI never imports a model class
  directly.

## 3.3 Modular, Backend-Dispatcher Architecture

Detection, Retrieval, and Refinement are each implemented as a thin
dispatcher over a `backends/` sub-package (an abstract base class plus
one or more concrete implementations). Swapping a backend is a one-line
config change; adding a new backend requires one new file plus one new
dispatch branch, and no other module changes. See
`02_MODULE_SPECIFICATION.md`, Appendix, for the exact contract.

## 3.4 Configuration Driven, No Exceptions

All runtime parameters — model paths, thresholds, plugin/backend
tuning surfaces, VAL stage/metric selection — live in
`configs/config.yaml`. This extends to the evidence-fusion layer:
`Reranker`'s per-plugin weights, retrieval-consensus protection curve,
and confusable-pair list are all configuration, not hard-coded
constants, specifically so that tuning the fusion strategy (an ongoing,
iterative process — see Section 10) never requires a code change.

## 3.5 Dual-Resolution Cropping

Every `CropImage` carries two pixel arrays: `image_array` (resized to
`cropping.target_size`, consumed exclusively by `Retriever`) and
`raw_image_array` (original resolution, boundary-clipped only, consumed
exclusively by OCR/Color/Barcode plugins). This exists because a single
shared resized crop was found to destroy small printed packaging text
before OCR ever saw it (Section 10, Experiment 3) — Retrieval and
Plugins have fundamentally different resolution needs and must not share
one preprocessed array.

## 3.6 Evidence Fusion Is Consensus-Aware, Not Additive

Early evidence fusion simply added a plugin's confidence boost to
whichever candidate was already winning. This was found to correct
roughly as many wrong decisions as it broke (Section 10, Experiment 6).
The current `Reranker` instead:

- Scales how much any single plugin is allowed to influence the outcome
  by how strongly Retrieval's own Top-K already agrees with itself
  (`rerank.retrieval_protection`) — weak evidence cannot flip a decision
  the retrieval index is confident about; strong evidence still can.
- Applies an extra agreement requirement for explicitly known-hard
  "confusable pairs" (`rerank.confusable_pairs`) — a product pair that
  historically gets confused requires more independent plugins to agree
  before the fused decision is trusted.

---

# 4. Project Structure

```text
stocktaking_ai/
├── README.md
├── requirements.txt
├── run.py
├── configs/
│   └── config.yaml
├── data/
│   ├── gallery/
│   ├── metadata/            # products.json, product_ids.json, product_colors.json
│   ├── benchmark/            # COCO images/ + _annotations.coco.json
│   ├── query/
│   ├── outputs/
│   └── cache/
├── weights/
├── scripts/
├── src/
│   ├── core/
│   ├── models/
│   ├── catalog/
│   ├── detection/            # detector.py, backends/, cropper.py
│   ├── pipeline/             # pipeline.py, overlap.py, build.py
│   ├── segmentation/          # refiner.py, backends/
│   ├── retrieval/              # retriever.py, backends/, gallery_builder.py
│   ├── decision/                # decision.py, reranker.py
│   ├── plugins/                  # manager.py, ocr.py, color.py, barcode.py
│   ├── storage/
│   ├── inference/
│   ├── validation/                # validate.py, evaluator.py, metrics.py
│   └── ui/
└── tests/
```

---

# 5. High-Level Architecture

```text
                 Desktop UI / CLI
                       │
                       ▼
             Inference / Validation Runner
                       │
                       ▼
              InventoryPipeline (orchestrator)
                       │
   ┌───────────────────┼───────────────────────────────┐
   ▼                   ▼                                ▼
Detection      OverlapResolver ──► Refiner (optional)  Retrieval
   │                   │                                │
   └───────────────────┴────────────┬───────────────────┘
                                    ▼
                              DecisionEngine
                                    │
                         ┌──────────┴──────────┐
                         │ needs_plugin?        │
                         └──────────┬──────────┘
                            NO      │      YES
                             │      ▼
                             │  PluginManager (OCR/Color/Barcode)
                             │      │
                             │      ▼
                             │  Reranker (evidence fusion)
                             └──────┬──────┘
                                    ▼
                             InventoryResult
                                    ▼
                             StorageManager
```

---

# 6. Current Scope (v0.1.0 extended)

## Implemented
- Full 8-stage runtime pipeline (Detection through Reranker/Fusion),
  with `run_with_trace()` for full-fidelity validation.
- Pluggable backends for Detection (mock_contour, RF-DETR), Retrieval
  (mock_visual_embedding, SigLIP2), Refinement (none, mock_refiner,
  SAM2).
- Dual-resolution cropping.
- Three-reason plugin trigger policy (uncertain / ambiguous / force).
- Evidence-driven Reranker with retrieval-consensus protection and a
  confusable-pair guard.
- 9-stage staged validation (VAL) sharing one detection↔ground-truth
  match per image across all stages, with a standalone metric-formula
  registry (`metrics.py`), full per-crop flat export (`records.csv`),
  color-coded annotated benchmark images, a per-stage metrics chart, and
  a full per-stage latency breakdown.
- Desktop UI with a Total-Items-Counted panel + per-product breakdown
  (this is a counting system, not just a classifier), zoomable
  annotated/gallery-match viewers, and a runtime similarity-threshold
  override that never reloads a model.

## Known-incomplete / actively being tuned
- Barcode plugin: currently disabled (near-zero decode success rate in
  practice; root cause not fully isolated — see `03_DEVELOPMENT_RULES.md`
  known-issues appendix).
- `products.json.barcode` is empty for essentially every catalog entry.
- `data/metadata/product_colors.json` is hand-authored, not generated by
  the offline build pipeline.
- SAM2 segmentation refinement provides only marginal net benefit and
  degrades roughly as many boxes as it improves.
- The confusable pair `7` vs `8` (differ by one printed character)
  remains the weakest point in the system even after evidence fusion.
- `confusable_min_agreeing_plugins` is temporarily set to `1` for active
  debugging; the intended production value is `2`.

## Explicitly excluded (deferred)
- Detector/Retriever/Plugin dependency-injection frameworks or
  registries beyond the existing lightweight dispatcher pattern.
- Distributed / real-time camera inference.
- Automatic color-reference generation from gallery photos (proposed,
  not implemented).
- Hungarian (optimal) matching in VAL — greedy IoU matching is used
  throughout by deliberate choice for v0.1.0 simplicity.

---

# 7. Project Vision

Architectural integrity and stage-level testability take priority over
feature volume. Every debugging session in this project's history (see
`03_DEVELOPMENT_RULES.md`, Appendix: Debugging History) was resolvable
specifically *because* each stage exposes its own inputs/outputs
independently through `PipelineTrace` — this property must be preserved
as the system evolves.