# STOCKTAKING AI
## DEVELOPMENT RULES

Version: 0.1.0 (extended)

---

# Purpose

Mandatory code standards and module interaction principles for every
file in `src/`. Whenever code and this document disagree, this document
wins.

---

# 1. General Principles

- Single Responsibility Principle, strictly per module (see
  `02_MODULE_SPECIFICATION.md`).
- Separation of Concerns: UI, business logic, pipeline control, model
  inference are never mixed in one file.
- Pipeline-Centered Design: `InventoryPipeline` is the only orchestrator.
- Configuration Driven: zero hard-coded thresholds/paths/weights,
  including evidence-fusion weights and VAL metric selection.
- DTO-Driven Communication: only typed dataclasses cross module
  boundaries, never raw dicts.

---

# 2. Python Environment

Python >= 3.11. Modern generic syntax (`list[str]`, `X | None`).

---

# 3-4. Formatting & Naming

PEP 8, Black, 88-char lines. `snake_case.py` files,
`snake_case` functions/variables, `PascalCase` classes,
`UPPER_CASE` constants, `_leading_underscore` private helpers.

---

# 5. Type Hints

Every public function/method fully typed. No untyped public interfaces.

---

# 6. Docstrings

Google-style, on every public class/method/module.

---

# 7. Structured Logging

`logging` via `core.logger` only. Never `print()`. INFO for lifecycle
milestones, WARNING for recoverable anomalies (e.g. "OCR orientation
ambiguous"), ERROR for failures, DEBUG for internal diagnostics (e.g.
per-candidate evidence scores in Reranker).

---

# 8. Configuration Management

All settings via `core.config` / `configs/config.yaml`. This explicitly
includes: evidence-fusion weights (`rerank.*`), retrieval-consensus
protection curve parameters, confusable-pair lists, VAL per-stage
metric selection. None of these may be hard-coded constants in source,
even during active tuning — see Rule 26.

---

# 9. Exception & Error Handling

Never silently swallow exceptions. Log with `logger.exception` and
re-raise, or handle a specific exception type with a documented
recovery path (e.g. `Refiner` falling back to the original detector bbox
when a segmentation backend raises).

---

# 10. Import Statements

Absolute imports from `src` root. No wildcard imports. Standard library
→ third-party → local, in that order.

---

# 11. Module Autonomy & Public API Boundary

Only designated public APIs may be called across module boundaries.
Private (`_`-prefixed) helpers are never reached into from another
module — including from `Reranker` into `DecisionEngine`, which is why
`evaluate_thresholds()` is a deliberately public, pure method rather
than a private helper Reranker reaches into.

---

# 12. Pipeline Orchestration Rules (pipeline.py)

Allowed: initialize every component once; execute stages in sequence;
pass DTOs between stages; branch on `needs_plugin`/`needs_refinement`.
Forbidden: AI inference logic, file I/O, UI rendering.

---

# 13-20. Per-Module Rules

See `02_MODULE_SPECIFICATION.md` for the authoritative, current
per-module responsibility/forbidden-behavior list (Sections 3-14 there).
This document does not duplicate it to avoid the two documents drifting
apart during active development.

---

# 21. Standardized DTOs

Only `src/models/models.py` dataclasses cross module boundaries.

---

# 22. Public API Standard Summary

```text
Detector.detect(image_data: ImageData) -> DetectionResult
OverlapResolver.resolve(detection_result: DetectionResult) -> OverlapResult
find_suspicious_pairs(detections, iou_threshold, overlap_ratio_threshold) -> list[OverlapPair]
Refiner.refine(image_array, detection_result: DetectionResult, overlap_result: OverlapResult) -> RefinementResult
Cropper.crop(image_data: ImageData, detection_result: DetectionResult, refinement_result: RefinementResult) -> list[CropImage]
Retriever.retrieve(crop: CropImage) -> RetrievalResult
DecisionEngine.decide(retrieval_result: RetrievalResult) -> DecisionResult
DecisionEngine.evaluate_thresholds(similarity: float, detection_confidence: float) -> tuple[str, float]
PluginManager.run_plugins(crop: CropImage, decision: DecisionResult) -> PluginResult
Reranker.rerank(retrieval_result: RetrievalResult, plugin_result: PluginResult) -> DecisionResult
InventoryPipeline.run(image_data: ImageData) -> InventoryResult
InventoryPipeline.run_with_trace(image_data: ImageData) -> tuple[InventoryResult, PipelineTrace]
```

---

# 23. Performance & Memory Management

Heavy weights loaded once in `__init__`, never in a hot-path method.
Exception explicitly permitted: `DecisionEngine`/`Reranker` may be
cheaply re-instantiated per call for a runtime threshold override, since
neither holds model weights.

---

# 24. Testability & Independence

Every module unit-testable in isolation with synthetic inputs (see
`tests/conftest.py`). This property is what made every debugging session
in the Appendix below tractable — preserve it.

---

# 25. Extensibility Standard

New Detection/Retrieval/Refinement backend: one new file in the
relevant `backends/`, one new dispatch branch, zero other file changes.
New VAL metric: one new function in `metrics.py`, registered in
`METRIC_REGISTRY`, referenced by name in `config.yaml` — zero
`evaluator.py` changes.

---

# 26. Absolute Forbidden Checklist

```text
❌ Never bypass InventoryPipeline.
❌ Never hard-code config values, paths, thresholds, or evidence-fusion weights.
❌ Never put AI model logic inside UI files.
❌ Never allow direct module-to-module dependencies outside the Pipeline flow.
❌ Never use print() statements or wildcard imports.
❌ Never catch exceptions without logging stack traces.
❌ Never reload model weights for every incoming query image.
❌ Never let ColorPlugin/OcrPlugin/BarcodePlugin read the product catalog
   or compare against reference values — that is Reranker's job only.
❌ Never let OverlapResolver remove or mutate a Detection (it is not NMS).
❌ Never let Refiner overwrite DetectionResult.
```

---

# Appendix: Debugging History (kept for context — do not repeat these mistakes)

1. **`product_id` identity bug**: a code path populated the catalog
   `product_id` field with the gallery folder name instead of the stable
   numeric ID. Every module that consumes `product_id` must treat it as
   an opaque numeric-string identifier — never assume it is
   human-readable or derivable from a display name.
2. **SigLIP2 unpooled-embedding bug**: `get_image_features()` returned
   per-patch features `(1, 196, 768)`, silently truncated to a single
   patch by naive reshaping downstream. Any new embedding backend must
   explicitly verify its output shape is `(1, hidden_dim)`, pooled, not
   `(1, num_patches, hidden_dim)` — do not assume a HuggingFace model
   method name implies the expected output shape.
3. **Shared-resolution crop bug**: OCR read a crop resized for Retrieval,
   destroying small text. This is why `CropImage` carries two
   resolutions (Rule: Section 3.5 in `01_PROJECT_CONTEXT.md`) — do not
   reintroduce a single shared crop resolution for a new plugin without
   considering this.
4. **Additive evidence-fusion bug**: naive confidence-boost addition in
   Reranker corrected roughly as many cases as it broke. Any new
   evidence source must integrate through the retrieval-consensus
   protection mechanism, not bypass it with a flat additive boost.
5. **Barcode plugin single-attempt bug**: the original plugin called `pyzbar.decode()` exactly
   once on a raw crop and gave up, which fails on almost any real-world rotation/contrast
   condition. Any adaptive-decode-style plugin (barcode, and potentially future OCR
   refinements) should follow the same pattern established here: a cheap presence/region
   pre-check before committing to expensive preprocessing, cumulative fallback stages that
   each retry the actual decode, and an early exit on first success — not a single
   best-effort attempt.