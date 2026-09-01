# **Stocktaking AI**

> AI-powered retail product inventory counting system — detects, segments, retrieves, and identifies individual products from shelf photos, fusing visual retrieval with OCR, Color, and Barcode evidence to resolve visually near-identical product variants.

## **📑 Table of Contents**

* [1\. Executive Summary](https://www.google.com/search?q=%231-executive-summary)  
* [2\. System Architecture & Pipeline](https://www.google.com/search?q=%232-system-architecture--pipeline)  
* [3\. Key Modules & Features](https://www.google.com/search?q=%233-key-modules--features)  
* [4\. Project Structure](https://www.google.com/search?q=%234-project-structure)  
* [5\. System Requirements](https://www.google.com/search?q=%235-system-requirements)  
* [6\. Installation & Environment Setup](https://www.google.com/search?q=%236-installation--environment-setup)  
* [7\. Dataset & Metadata Specification](https://www.google.com/search?q=%237-dataset--metadata-specification)  
* [8\. Configuration Schema](https://www.google.com/search?q=%238-configuration-schema)  
* [9\. System Execution Guide](https://www.google.com/search?q=%239-system-execution-guide)  
* [10\. Input & Output Specifications](https://www.google.com/search?q=%2310-input--output-specifications)  
* [11\. Quantitative Performance Evaluation](https://www.google.com/search?q=%2311-quantitative-performance-evaluation)  
* [12\. Engineering Insights & Experimental Log](https://www.google.com/search?q=%2312-engineering-insights--experimental-log)  
* [13\. Current Runtime Status](https://www.google.com/search?q=%2313-current-runtime-status)  
* [14\. Technical Limitations](https://www.google.com/search?q=%2314-technical-limitations)  
* [15\. System Roadmap](https://www.google.com/search?q=%2315-system-roadmap)  
* [16\. Citation](https://www.google.com/search?q=%2316-citation)  
* [17\. License](https://www.google.com/search?q=%2317-license)

## **1\. Executive Summary**

**Stocktaking AI** is an enterprise-grade Computer Vision system designed to automate retail shelf inventory auditing from a single photograph. Given a high-density shelf image, the system executes a multi-stage inference pipeline:

1. **Class-Agnostic Localization (Object Detection):** Identifies bounding coordinates for all product instances regardless of class.  
2. **Boundary Refinement & Overlap Analysis:** Detects spatial occlusions and refines instance boundaries using Segment Anything Model 2 (SAM2).  
3. **Visual Representation & Retrieval:** Maps image crops into a high-dimensional vector space via SigLIP2 and retrieves top-K candidate identities using FAISS vector indexing.  
4. **Multimodal Evidence Fusion & Reranking:** Fuses auxiliary attributes—OCR text tokens, color profiles in Lab color space (CIEDE2000), and barcode data—to disambiguate fine-grained variants (e.g., identical packaging differing only by shade, net weight, or minor text).  
5. **Audit Trail & Inventory Synthesis:** Generates item counts, per-SKU breakdowns, and a complete decision audit trail for compliance and reporting.

The system enforces a decoupled architecture between **Localization** (detection/segmentation) and **Identification** (retrieval/evidence fusion). This modular design allows independent optimization, component swapping, and granular error isolation.

## **2\. System Architecture & Pipeline**

Plaintext  
Input Image  
  │  
  ▼  
① DETECTION STAGE               Detector (RF-DETR / Mock Contour)  
  │                             — Class-agnostic bounding box extraction  
  ▼  
② OVERLAP ANALYSIS STAGE        OverlapResolver  
  │                             — Flags high-density occlusion groups  
  ▼  
③ SEGMENTATION STAGE           Refinement Engine (SAM2 / Mock / None)  
  │                             — Boundary mask refinement on flagged regions  
  ▼  
④ CROPPING STAGE                Cropper  
  │                             — Generates dual outputs: Resized Crop (Retrieval)  
  │                               & Raw High-Res Crop (Secondary Plugins)  
  ▼  
⑤ VISUAL RETRIEVAL STAGE        Retriever (SigLIP2 Vector Extraction \+ FAISS)  
  │                             — Top-K nearest gallery item retrieval  
  ▼  
⑥ DECISION STAGE               Decision Engine  
  │                             — Evaluates similarity score against thresholds  
  │                             — Emits plugin trigger flags (uncertain/ambiguous/force)  
  ▼  
⑦ SECONDARY EVIDENCE STAGE     Plugin Manager (OCR / Color / Barcode)  
  │                             — Extracts localized domain evidence on demand  
  ▼  
⑧ RERANKING & FUSION STAGE     Reranker Engine  
  │                             — Multi-evidence fusion across Top-K candidates  
  │                             — Enforces Consensus Protection & Confusable-Pair Guards  
  ▼  
OUTPUT SYNTHESIS                Storage Manager (Exports JSON / CSV / Annotated Visuals)

The pipeline supports two execution modalities:

* InventoryPipeline.run(): Production-grade inference optimizing latency and memory overhead.  
* InventoryPipeline.run\_with\_trace(): Extended execution path capturing full state traces across all 9 pipeline stages for benchmarking and diagnostic evaluation.

## **3\. Key Modules & Features**

* **Pluggable Backend Framework:** Decoupled interfaces allow seamless backend substitution via configuration:  
  * *Detection Backends:* rf\_detr (Deep learning inference) / mock\_contour (Classical CV fallback).  
  * *Retrieval Backends:* siglip2 (Vision-Language embeddings) / mock\_visual\_embedding (HSV histograms).  
  * *Segmentation Backends:* sam2 (Instance segmentation) / none.  
* **Dual-Resolution Cropping Engine:** Generates a standardized, resized tensor for feature vector extraction alongside an uncompressed high-resolution crop for fine-grained OCR and barcode extraction.  
* **Dynamic Plugin Trigger Policies:** Supports three deterministic trigger criteria:  
  * uncertain: Similarity score falls within border thresholds.  
  * ambiguous: Top-N retrieval candidates exhibit tight cosine margin deltas.  
  * force: Enforces hardcoded domain rules for specific product classes requiring multi-modal verification.  
* **Multi-Modal Evidence Reranking:**  
  * *Barcode Processing:* Adaptive 9-stage decoding pipeline optimized for low-resolution, warped, or rotated crops.  
  * *OCR Processing:* Multi-orientation text parsing (0°, 90°, 180°, 270°) with CLAHE contrast transformation and token matching.  
  * *Color Analysis:* Color extraction in CIELAB space evaluated against reference standards via the CIEDE2000 metric.  
  * *Consensus & Guard Mechanisms:* Protects high-confidence visual retrieval results from noisy plugin overrides while applying strict validation rules to known confusable product pairs.  
* **9-Stage Validation Suite:** Integrated framework measuring performance across individual stages—from raw object detection to End-to-End SKU classification—with automated diagnostic reporting.  
* **Desktop Graphical Interface (Tkinter UI):** Real-time monitoring dashboard supporting SKU counting visualization, dynamic threshold tuning, visual inspection, and validation trace analysis.

## **4\. Project Structure**

Plaintext  
stocktaking\_ai/  
├── README.md  
├── requirements.txt  
├── run.py                       \# CLI entry point (Build \-\> Infer / Validate / UI)  
├── configs/  
│   └── config.yaml              \# Single source of truth for runtime config  
├── data/  
│   ├── gallery/                 \# Product gallery reference photos  
│   ├── metadata/  
│   │   ├── products.json        \# Compiled product catalog  
│   │   ├── product\_ids.json     \# SKU string to integer ID mappings  
│   │   └── product\_colors.json  \# Reference color metadata (CIELAB/RGB)  
│   ├── benchmark/  
│   │   ├── images/              \# Benchmark dataset images  
│   │   └── \_annotations.coco.json  
│   ├── query/                   \# Input query images  
│   ├── outputs/                 \# Exported artifacts (JSON, CSV, Visuals, Charts)  
│   └── cache/                   \# Serialized FAISS index files and cache  
├── weights/                     \# Pre-trained deep learning checkpoints  
├── scripts/  
│   └── generate\_sample\_data.py  
├── src/  
│   ├── core/                    \# Core utilities, logging, configuration loading  
│   ├── models/                  \# Domain Data Transfer Objects (DataClasses / Pydantic)  
│   ├── catalog/                 \# Metadata build pipeline and ID assignment  
│   ├── detection/  
│   │   ├── detector.py          \# Detection abstraction layer  
│   │   ├── backends/            \# RF-DETR and contour implementations  
│   │   └── cropper.py           \# Dual-resolution crop generation  
│   ├── pipeline/  
│   │   ├── pipeline.py          \# Master pipeline orchestrator  
│   │   ├── overlap.py           \# Occlusion and overlap resolution  
│   │   └── build.py             \# Offline metadata/indexing build tasks  
│   ├── segmentation/  
│   │   ├── refiner.py           \# Boundary refinement dispatcher  
│   │   └── backends/            \# SAM2 and mock implementations  
│   ├── retrieval/  
│   │   ├── retriever.py         \# Retrieval dispatcher (Runtime inference)  
│   │   ├── backends/            \# SigLIP2 and mock visual embedding  
│   │   └── gallery\_builder.py   \# FAISS index construction pipeline  
│   ├── decision/  
│   │   ├── decision.py          \# Decision engine and trigger policies  
│   │   └── reranker.py          \# Evidence fusion and reranking logic  
│   ├── plugins/  
│   │   ├── manager.py           \# Plugin lifecycle management  
│   │   ├── ocr.py               \# OCR text extraction and normalization  
│   │   ├── color.py             \# CIELAB color extraction and matching  
│   │   └── barcode.py           \# Barcode detection and adaptive decoding  
│   ├── storage/                 \# Data persistence and visual reporting  
│   ├── inference/               \# Production inference execution engine  
│   ├── validation/  
│   │   ├── validate.py          \# Benchmark evaluation orchestrator  
│   │   ├── evaluator.py         \# Stage-by-stage metric computation  
│   │   └── metrics.py           \# Mathematical metric formulations  
│   └── ui/                      \# Desktop GUI application  
└── tests/                       \# Unit and integration test suites

## **5\. System Requirements**

* **Operating System:** Linux (Ubuntu 20.04/22.04 LTS recommended) / macOS / Windows 11  
* **Runtime Environment:** Python \>= 3.11  
* **Core Dependencies:** numpy, opencv-python-headless, PyYAML, pydantic, Pillow, matplotlib, faiss-cpu  
* **Deep Learning Frameworks:** torch, torchvision, transformers, rfdetr, supervision, sam2  
* **Domain Tools:** easyocr, pyzbar (requires system-level libzbar0), tkinter  
* **Hardware Acceleration:** NVIDIA GPU with \>= 8GB VRAM (CUDA execution recommended for RF-DETR, SAM2, and SigLIP2).

## **6\. Installation & Environment Setup**

Bash  
\# Clone repository and install core dependencies  
git clone \<repository-url\>  
cd stocktaking\_ai  
pip install \-r requirements.txt \--break-system-packages

\# Install system-level dependency for barcode decoding  
sudo apt-get install \-y libzbar0

**Model Checkpoint Placement:** Neural network weights must be placed in designated directories prior to execution:

| Model / Subsystem | Required Checkpoint Path |
| :---- | :---- |
| RF-DETR Detector | weights/detector/checkpoint\_best\_ema.pth |
| SAM2 Refinement Model | weights/refinement/sam2/sam2.1\_hiera\_small.pt |
| SigLIP2 Vision Encoder | Pulled automatically from Hugging Face (google/siglip2-base-patch16-224) |

## **7\. Dataset & Metadata Specification**

* **Gallery Store (data/gallery/):** Organised directory structure containing reference images per SKU. Directory names map directly to product descriptors defined in configs/config.yaml.  
* **Color Standards (data/metadata/product\_colors.json):** Defines canonical color standards in RGB/CIELAB space for variant disambiguation:

JSON  
{  
  "PK300": {  
    "name": "PK300",  
    "rgb": \[109, 63, 62\],  
    "hex": "\#6D3F3E"  
  }  
}

* **Benchmark Dataset (data/benchmark/):** Standardized COCO format object detection annotations. The category\_id attribute maps directly to the system's internal numeric product\_id.

## **8\. Configuration Schema**

All runtime parameters are centralized within configs/config.yaml. Main configuration blocks:

| Parameter Block | Functional Scope |
| :---- | :---- |
| catalog | SKU ID mapping rules and catalog compilation toggles |
| detection | Backend choice, confidence thresholds, IoU limits, and detector parameters |
| refinement | SAM2 invocation triggers and geometric boundary limits |
| cropping | Bounding box expansion padding and target tensor resolution |
| retrieval | Feature vector dimensionality, FAISS parameters, and Top-K limits |
| decision | Acceptance, uncertainty, and rejection similarity thresholds |
| plugins | Configuration parameters for OCR, Color, Barcode, and rule overrides |
| rerank | Multi-evidence weighting factors, ΔE color thresholds, and protection rules |
| storage | Output format specifications and visual annotation styling |
| validation | IoU evaluation thresholds, stage toggles, and report exports |

## **9\. System Execution Guide**

The primary entry point run.py automatically builds necessary indexes and metadata prior to command execution (bypassable via \--skip-build).

Bash  
\# 1\. Execute single-image inference  
python run.py \--mode infer \--image data/query/test\_shelf.jpg

\# 2\. Execute batch inference across a directory  
python run.py \--mode infer \--image-dir data/query/

\# 3\. Run validation suite against a COCO benchmark dataset  
python run.py \--mode validate \--benchmark-dir data/benchmark/

\# 4\. Launch Desktop Graphical Interface  
python run.py \--mode ui

Programmatic Python API usage:

Python  
from src.core.config import load\_config  
from src.pipeline.pipeline import InventoryPipeline

config \= load\_config()  
pipeline \= InventoryPipeline(config)

\# Standard inference execution  
result \= pipeline.run(image\_data)

\# Diagnostic execution with complete stage tracing  
result, trace \= pipeline.run\_with\_trace(image\_data)

## **10\. Input & Output Specifications**

| Operational Mode | Input Format | Generated Artifacts (data/outputs/) |
| :---- | :---- | :---- |
| Inference | Image file / Directory | result.json (Full audit log), result.csv (Item counts), result.jpg (Visual annotations) |
| Validation | COCO Benchmark Dir | report.json/csv (9-stage metrics), records.csv (Instance-level logs), Diagnostic Visuals & Charts |
| GUI Application | Interactive User Input | Real-time counting tables, annotated overlay, dynamic threshold sliders |

## **11\. Quantitative Performance Evaluation**

Performance benchmarks on the standard test dataset (18 SKUs, 31 shelf scenes, 293 annotated instances):

| Pipeline Stage | Primary Metric | Measured Value |
| :---- | :---- | :---- |
| Detection | F1-Score (Class-Agnostic, BBox IoU) | **0.950** |
| Cropping | Crop Validity Rate | **1.000** |
| Overlap Analysis | F1-Score | **0.920** |
| Segmentation (SAM2) | Mean IoU Improvement | **\+0.011** |
| Visual Retrieval | Top-1 Accuracy | **0.739** |
| Visual Retrieval | Top-5 Accuracy (K=5) | **0.990** |
| Decision Engine | Precision (Pre-Evidence) | **0.739** |
| Evidence Fusion | Accuracy Delta | **\+0.224** |
| **End-to-End Pipeline** | **Overall F1-Score** | **0.901** |

The multi-evidence reranking architecture resolved **59 visual retrieval misclassifications** without introducing **any false corrections (0 regressions)**, driving post-fusion accuracy to **93.9%** and the End-to-End F1-Score to **0.901**.

## **12\. Engineering Insights & Experimental Log**

1. **SigLIP2 Pooling Optimization:** Resolved an issue where unpooled patch embeddings (1, 196, 768\) were returned instead of a global feature vector (1, 768\). Implementing mean-pooling across the spatial patch dimension increased **Top-1 Retrieval Accuracy from 3% to 55%**.  
2. **Dual-Resolution Crop Architecture:** Addressed text degradation caused by uniform downsampling during visual feature extraction. Introducing dedicated uncompressed crops (raw\_image\_array) for plugin execution improved **OCR recognition rates from 54% to 98%**.  
3. **Multi-Orientation OCR Processing:** Implemented multi-angle rotation sweeps (0°, 90°, 180°, 270°) with Contrast Limited Adaptive Histogram Equalization (CLAHE) to handle arbitrarily oriented products on shelf displays.  
4. **Guarded Reranking Architecture:** Designed **Retrieval Consensus Protection** and **Confusable-Pair Guard** logic to prevent secondary plugin noise from overriding high-confidence visual vector matches, achieving a net accuracy gain of \+22.4%.  
5. **Adaptive Barcode Decoding Pipeline:** Engineered a 9-stage adaptive decoding pipeline utilizing gradient anisotropy for barcode localization, region-seeded deskewing, and adaptive thresholding to process blurred or off-angle codes.

## **13\. Current Runtime Status**

* **Barcode Subsystem Status:** The upgraded adaptive barcode decoding engine is currently **disabled in runtime configuration (plugins.barcode.enabled: false)** pending comprehensive re-validation.  
* **Catalog Metadata Integration:** The products.json metadata catalog requires population of canonical GTIN/Barcode values before decoded barcode evidence can actively contribute to fusion score computation.

## **14\. Technical Limitations**

* Fine-grained identification errors (\~6%) are concentrated among near-identical packaging variants differing solely by minor text descriptors (e.g., net weight variants). These cases trigger ambiguous flags for manual review.  
* The reference color database (product\_colors.json) relies on manual configuration rather than automated extraction from gallery datasets.  
* The OCR text recognition module is optimized for Latin alphanumeric characters (language: \[en\]) and does not process non-Latin packaging scripts.  
* CPU execution is fully supported but exhibits higher processing latency compared to CUDA GPU acceleration.

## **15\. System Roadmap**

* Automate reference color extraction directly from gallery photos using localized K-Means clustering in CIELAB space.  
* Re-validate and enable the adaptive barcode plugin within the evidence fusion pipeline following complete metadata integration.  
* Refine SAM2 segmentation trigger heuristics to optimize boundary quality for tightly packed shelf displays.  
* Expand OCR language coverage to support multilingual retail packaging.

## **16\. Citation**

Internal research and development codebase. Not currently tied to an external academic publication.

## **17\. License**

Proprietary internal software. Consult organizational licensing terms prior to external distribution.

