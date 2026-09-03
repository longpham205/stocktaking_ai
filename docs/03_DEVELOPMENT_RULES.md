# STOCKTAKING AI

## DEVELOPMENT RULES

**Version:** 0.1.0 (extended)

---

# Purpose

Mandatory code standards and module interaction principles for every file in `src/`.

Whenever code and this document disagree, **this document wins**.

---

# 1. General Principles

- **Single Responsibility Principle:** strictly per module (see `02_MODULE_SPECIFICATION.md`).
- **Separation of Concerns:** UI, business logic, pipeline control, and model inference are never mixed in one file.
- **Pipeline-Centered Design:** `InventoryPipeline` is the only orchestrator.
- **Configuration Driven:** zero hard-coded thresholds, paths, weights, including evidence-fusion weights and VAL metric selection.
- **DTO-Driven Communication:** only typed dataclasses cross module boundaries, never raw `dict`.

---

# 2. Python Environment

- Python `>= 3.11`
- Use modern generic syntax:
  - `list[str]`
  - `X | None`

---

# 3. Formatting & Naming

Follow **PEP 8** and **Black** formatting.

- Maximum line length: **88 characters**
- Python files: `snake_case.py`
- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_CASE`
- Private helpers: `_leading_underscore`

---

# 4. Type Hints

Every public function and method must be fully typed.

**Forbidden:**

- Untyped public interfaces
- Missing return type annotations
- Missing parameter type annotations

---

# 5. Docstrings

Use **Google-style docstrings** on:

- Every public module
- Every public class
- Every public method

---

# 6. Structured Logging

Use Python `logging` through `core.logger` only.

**Never use `print()` in production code.**

Log levels:

| Level | Usage |
|---|---|
| `INFO` | Lifecycle milestones |
| `WARNING` | Recoverable anomalies, e.g. `"OCR orientation ambiguous"` |
| `ERROR` | Failures that can be handled or reported |
| `DEBUG` | Internal diagnostics, e.g. per-candidate evidence scores in `Reranker` |

---

# 7. Configuration Management

All configuration must be loaded through:

- `core.config`
- `configs/config.yaml`

This explicitly includes:

- Evidence-fusion weights: `rerank.*`
- Retrieval-consensus protection curve parameters
- Confusable-pair lists
- VAL per-stage metric selection
- Thresholds
- Model weights
- Paths

None of these values may be hard-coded as source-level constants, even during active tuning.

See **Rule 18**.

---

# 8. Exception & Error Handling

Never silently swallow exceptions.

When an unexpected exception occurs:

1. Log the stack trace with `logger.exception()`.
2. Re-raise the exception, **or**
3. Handle a specific exception type with a documented recovery path.

### Example

`Refiner` may fall back to the original detector bounding box when a segmentation backend raises an exception.

The recovery behavior must be explicit and documented.

---

# 9. Import Statements

Use **absolute imports from the `src` root**.

### Import order

1. Standard library
2. Third-party packages
3. Local `src` modules

### Forbidden

- Wildcard imports: `from module import *`
- Relative imports when an absolute import is available

---

# 10. Module Autonomy & Public API Boundary

Only designated **public APIs** may be called across module boundaries.

Private (`_`-prefixed) helpers must never be accessed from another module.

This includes cases such as `Reranker` reaching into `DecisionEngine`.

For example, `evaluate_thresholds()` is intentionally a **public, pure method** rather than a private helper because it is part of the permitted public API.

---

# 11. Pipeline Orchestration Rules

## `pipeline.py`

`InventoryPipeline` is the only component responsible for orchestrating the complete pipeline.

### Allowed

- Initialize every component once
- Execute stages in sequence
- Pass DTOs between stages
- Branch on:
  - `needs_plugin`
  - `needs_refinement`

### Forbidden

- AI inference logic
- File I/O
- UI rendering
- Module-specific business logic

---

# 12. Per-Module Rules

See `02_MODULE_SPECIFICATION.md` for the authoritative and current per-module responsibility and forbidden-behavior list.

This document intentionally does not duplicate those rules to prevent the two documents from drifting apart during active development.

---

# 13. Standardized DTOs

Only dataclasses defined in:

```text
src/models/models.py
```

may cross module boundaries.

Modules must not exchange raw dictionaries as their primary interface.

---

# 14. Public API Standard Summary

```text
Detector.detect(
    image_data: ImageData
) -> DetectionResult

OverlapResolver.resolve(
    detection_result: DetectionResult
) -> OverlapResult

find_suspicious_pairs(
    detections,
    iou_threshold,
    overlap_ratio_threshold
) -> list[OverlapPair]

Refiner.refine(
    image_array,
    detection_result: DetectionResult,
    overlap_result: OverlapResult
) -> RefinementResult

Cropper.crop(
    image_data: ImageData,
    detection_result: DetectionResult,
    refinement_result: RefinementResult
) -> list[CropImage]

Retriever.retrieve(
    crop: CropImage
) -> RetrievalResult

DecisionEngine.decide(
    retrieval_result: RetrievalResult
) -> DecisionResult

DecisionEngine.evaluate_thresholds(
    similarity: float,
    detection_confidence: float
) -> tuple[str, float]

PluginManager.run_plugins(
    crop: CropImage,
    decision: DecisionResult
) -> PluginResult

Reranker.rerank(
    retrieval_result: RetrievalResult,
    plugin_result: PluginResult
) -> DecisionResult

InventoryPipeline.run(
    image_data: ImageData
) -> InventoryResult

InventoryPipeline.run_with_trace(
    image_data: ImageData
) -> tuple[InventoryResult, PipelineTrace]
```

---

# 15. Performance & Memory Management

Heavy model weights must be loaded **once in `__init__`**.

They must never be loaded inside a hot-path method.

### Exception

`DecisionEngine` and `Reranker` may be cheaply re-instantiated per call when a runtime threshold override is required.

This exception is permitted because neither component holds model weights.

---

# 16. Testability & Independence

Every module must be unit-testable in isolation using synthetic inputs.

See:

```text
tests/conftest.py
```

This property is essential for debugging and must be preserved when extending the system.

---

# 17. Extensibility Standard

## New Detection / Retrieval / Refinement Backend

Adding a new backend should require:

1. One new file in the relevant `backends/` directory.
2. One new dispatch branch.
3. Zero unrelated file changes.

## New VAL Metric

Adding a new validation metric should require:

1. One new function in `metrics.py`.
2. Registration in `METRIC_REGISTRY`.
3. Reference by name in `config.yaml`.
4. **Zero changes to `evaluator.py`.**

---

# 18. Absolute Forbidden Checklist

The following rules are absolute:

- ❌ Never bypass `InventoryPipeline`.
- ❌ Never hard-code configuration values, paths, thresholds, or evidence-fusion weights.
- ❌ Never put AI model logic inside UI files.
- ❌ Never allow direct module-to-module dependencies outside the Pipeline flow.
- ❌ Never use `print()` statements.
- ❌ Never use wildcard imports.
- ❌ Never catch exceptions without logging stack traces.
- ❌ Never reload model weights for every incoming query image.
- ❌ Never let `ColorPlugin`, `OcrPlugin`, or `BarcodePlugin` read the product catalog or compare against reference values. That is `Reranker`'s responsibility only.
- ❌ Never let `OverlapResolver` remove or mutate a `Detection`. It is **not NMS**.
- ❌ Never let `Refiner` overwrite `DetectionResult`.

---

# Appendix: Debugging History

> This section is kept for context. Do not repeat these mistakes.

## 1. `product_id` Identity Bug

A code path populated the catalog `product_id` field with the gallery folder name instead of the stable numeric ID.

Every module that consumes `product_id` must treat it as an **opaque numeric-string identifier**.

Never assume that `product_id`:

- is human-readable;
- can be derived from a display name;
- is identical to a gallery folder name.

---

## 2. SigLIP2 Unpooled-Embedding Bug

`get_image_features()` returned per-patch features:

```text
(1, 196, 768)
```

These were silently truncated to a single patch by naive reshaping downstream.

Any new embedding backend must explicitly verify that its output shape is:

```text
(1, hidden_dim)
```

The embedding must be **pooled**, not:

```text
(1, num_patches, hidden_dim)
```

Do not assume that a Hugging Face model method name implies the expected output shape.

---

## 3. Shared-Resolution Crop Bug

OCR previously received a crop resized for Retrieval, which destroyed small text.

This is why `CropImage` carries **two resolutions**.

See:

```text
01_PROJECT_CONTEXT.md
Section 3.5
```

Do not reintroduce a single shared crop resolution for a new plugin without explicitly considering the requirements of that plugin.

---

## 4. Additive Evidence-Fusion Bug

A naive confidence-boost addition in `Reranker` corrected approximately as many cases as it broke.

Therefore, any new evidence source must integrate through the **retrieval-consensus protection mechanism**.

### Forbidden

Bypassing the protection mechanism with a flat additive boost.

---

## 5. Barcode Plugin Single-Attempt Bug

The original barcode plugin called:

```python
pyzbar.decode()
```

exactly once on a raw crop and gave up.

This failed under common real-world conditions such as:

- Rotation
- Low contrast
- Different orientations
- Image quality variations

Any adaptive-decode-style plugin, including barcode and potential future OCR refinements, should follow the established pattern:

1. Perform a cheap presence/region pre-check.
2. Commit to expensive preprocessing only when appropriate.
3. Execute cumulative fallback stages.
4. Retry the **actual decode operation** at each stage.
5. Exit early on the first successful decode.

### Forbidden

A single best-effort decode attempt is not sufficient for adaptive decoding.
