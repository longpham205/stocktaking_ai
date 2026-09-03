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
bottleneck.

The complete inventory process consists of:

- Product Detection (class-agnostic)
- Overlap Analysis
- Segmentation Refinement (optional, geometry-triggered)
- Product Cropping (dual-resolution)
- Image Retrieval (Top-K visual similarity via SigLIP2 + FAISS)
- Decision (accept/uncertain/reject + plugin trigger policy)
- Plugin Evidence Gathering (OCR / Color / Barcode, optional)
- Evidence Fusion / Reranking
- Inventory Result Generation & Export

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
- Provide a desktop UI (Tkinter) for demonstration and interactive validation
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
constants, specifically so that tuning the fusion strategy never requires a code change.

## 3.5 Dual-Resolution Cropping

Every `CropImage` carries two pixel arrays: `image_array` (resized to
`cropping.target_size`, consumed exclusively by `Retriever`) and
`raw_image_array` (original resolution, boundary-clipped only, consumed
exclusively by OCR/Color/Barcode plugins). This exists because a single
shared resized crop was found to destroy small printed packaging text
before OCR ever saw it — Retrieval and Plugins have fundamentally 
different resolution needs and must not share one preprocessed array.

## 3.6 Evidence Fusion Is Consensus-Aware, Not Additive

Early evidence fusion simply added a plugin's confidence boost to
whichever candidate was already winning. This was found to correct
roughly as many wrong decisions as it broke. The current `Reranker` instead:

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
├── .env
├── .gitignore
├── README.md
├── requirements.txt
├── setup.bat                    # One-click E2E setup script for Windows
├── setup.sh                     # Automated setup script for Linux
├── setup.command                # One-click setup script for macOS
├── run.py                       # CLI entry point (Build -> Infer / Validate / UI)
├── test.py                      # Rapid manual testing entry script
├── assets_manifest.json         # Checksum and structure integrity manifest
├── configs/
│   └── config.yaml              # Master runtime configuration
├── data/
│   ├── gallery/                 # Reference product images for vector indexing
│   ├── metadata/                # SKU catalogs, color maps, and ID mappings
│   ├── benchmark/               # COCO-formatted evaluation datasets
│   ├── query/                   # Input shelf images for inference
│   ├── outputs/                 # Exported results (JSON, CSV, annotated visuals)
│   └── cache/                   # Serialized FAISS vector index & metadata cache
├── debug/                       # Standalone diagnostic and verification scripts
├── docs/                        # Architecture specs, context, and developer guidelines
├── notebooks/                   # Analytical and pipeline evaluation Jupyter notebooks
├── scripts/
│   ├── setup.py                 # Core environment and asset initialization logic
│   ├── generate_manifest.py     # Asset manifest generation script
│   └── verify_manifest.py       # Integrity verification script
├── weights/                     # Model checkpoints (RF-DETR, SAM2, SigLIP2)
│   ├── detector/                # Detection model weights
│   ├── refinement/              # Segmentation model weights
│   └── retriever/               # Vision encoder offline weights
├── src/
│   ├── catalog/                 # Metadata compilation and catalog indexing
│   ├── core/                    # System configuration, logging, and common utilities
│   ├── decision/                # Similarity thresholding & multi-evidence reranking
│   ├── detection/               # Object detection backends and dual-crop generation
│   ├── inference/               # Production batch/single-image inference engine
│   ├── models/                  # Domain Data Transfer Objects (Pydantic / Dataclasses)
│   ├── pipeline/                # Master orchestrator, overlap resolution, and offline build
│   ├── plugins/                 # Secondary evidence plugins (OCR, Color, Barcode)
│   ├── retrieval/               # SigLIP2 embedding extraction and FAISS indexer
│   ├── segmentation/            # SAM2 instance mask boundary refinement
│   ├── storage/                 # CSV/JSON output persistence and visualization overlay
│   ├── ui/                      # Desktop Graphical Interface (Tkinter dashboard)
│   └── validation/              # 9-stage evaluation suite and metric calculators
└── tests/                       # Unit and integration pytest test suite

# 5. High-Level Architecture

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

# **6\. Current Scope (v0.1.0 extended)**

## **Implemented**

* Full 8-stage runtime pipeline (Detection through Reranker/Fusion), with `run_with_trace()` for full-fidelity validation.  
* Pluggable backends for Detection (mock\_contour, RF-DETR), Retrieval (mock\_visual\_embedding, SigLIP2), Refinement (none, mock\_refiner, SAM2).  
* Automated cross-platform setup scripts (`setup.bat`, `setup.sh`, `setup.command`).  
* Dual-resolution cropping.  
* Three-reason plugin trigger policy (uncertain / ambiguous / force).  
* Evidence-driven Reranker with retrieval-consensus protection and a confusable-pair guard.  
* 9-stage staged validation (VAL) sharing one detection↔ground-truth match per image across all stages, with a standalone metric-formula registry (`metrics.py`), full per-crop flat export (`records.csv`), color-coded annotated benchmark images, a per-stage metrics chart, and a full per-stage latency breakdown.  
* Desktop UI with a Total-Items-Counted panel \+ per-product breakdown (this is a counting system, not just a classifier), zoomable annotated/gallery-match viewers, and a runtime similarity-threshold override that never reloads a model.

## **Known-incomplete / actively being tuned**

* Barcode plugin: currently disabled in config (`plugins.barcode.enabled: false`) pending comprehensive re-validation and catalog metadata integration.  
* `products.json.barcode` is empty for essentially every catalog entry.  
* `data/metadata/product_colors.json` is hand-authored, not generated by the offline build pipeline.  
* SAM2 segmentation refinement provides only marginal net benefit and degrades roughly as many boxes as it improves.  
* Fine-grained identification errors (\~6%) remain concentrated among near-identical packaging variants differing solely by minor text descriptors (e.g., net weight).

## **Explicitly excluded (deferred)**

* Detector/Retriever/Plugin dependency-injection frameworks or registries beyond the existing lightweight dispatcher pattern.  
* Distributed / real-time camera inference.  
* Automatic color-reference generation from gallery photos (proposed, not implemented).  
* Hungarian (optimal) matching in VAL — greedy IoU matching is used throughout by deliberate choice for v0.1.0 simplicity.

# **7\. Project Vision**

Architectural integrity and stage-level testability take priority over feature volume. Every debugging session in this project's history was resolvable specifically *because* each stage exposes its own inputs/outputs independently through `PipelineTrace` — this property must be preserved as the system evolves.


