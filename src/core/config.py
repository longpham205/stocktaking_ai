"""Configuration loading and validation utilities.

This module is the single entry point for reading `configs/config.yaml`
into strongly-typed, validated configuration objects. No other module is
allowed to read the YAML file directly (see 03_DEVELOPMENT_RULES.md, Rule 8).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class AppSection(BaseModel):
    """General application metadata."""

    name: str
    version: str
    environment: str
    device: str


class LoggingSection(BaseModel):
    """Logging configuration."""

    level: str
    log_dir: str
    log_to_file: bool
    log_to_console: bool
    max_bytes: int
    backup_count: int


class PathsSection(BaseModel):
    """Centralized filesystem path configuration."""

    gallery_dir: str
    benchmark_dir: str
    benchmark_images_dir: str
    benchmark_labels_dir: str
    query_dir: str
    output_dir: str
    cache_dir: str
    metadata_dir: str
    detector_weights_dir: str
    retriever_weights_dir: str
    plugin_weights_dir: str
    refinement_weights_dir: str = "weights/refinement"


class CatalogSection(BaseModel):
    """Product catalog / metadata build configuration.

    ``id_mapping`` is the authoritative source of stable internal product
    IDs (see MetadataBuilder): keys are numeric-string IDs, values are the
    gallery folder name / product display name that ID is bound to.
    """

    build_metadata: bool
    products_filename: str
    product_ids_filename: str
    id_mapping: dict[str, str] = Field(default_factory=dict)


class RfDetrInferenceSection(BaseModel):
    """RF-DETR runtime inference optimization flags."""

    optimize: bool = True
    compile: bool = False
    batch_size: int = 1
    dtype: str = "float32"  # float32 | float16
    inplace: bool = False


class RfDetrSection(BaseModel):
    """RF-DETR real neural detection backend configuration."""

    variant: str = "base"
    weights_path: str = ""
    device: str = "cpu"
    inference: RfDetrInferenceSection = Field(default_factory=RfDetrInferenceSection)


class DetectionSection(BaseModel):
    """Object detection module configuration."""

    backend: str
    weights_path: str
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    min_box_area_ratio: float
    max_box_area_ratio: float
    min_aspect_ratio: float
    max_aspect_ratio: float
    canny_threshold_1: int
    canny_threshold_2: int
    blur_kernel_size: int
    max_detections: int
    rf_detr: RfDetrSection = Field(default_factory=RfDetrSection)


class RefinementTriggerSection(BaseModel):
    """Geometry-based policy deciding when segmentation refinement activates."""

    enabled: bool = True
    iou_threshold: float = 0.05
    overlap_ratio_threshold: float = 0.20
    min_overlapping_pairs: int = 1
    require_multiple_detections: bool = True


class Sam2Section(BaseModel):
    """SAM2 segmentation/refinement backend configuration."""

    model_type: str = "sam2.1_hiera_small"
    checkpoint_path: str = "weights/refinement/sam2/sam2.1_hiera_small.pt"
    model_config: str = ""
    device: str = "cpu"
    prompt_type: str = "box"
    use_source_image: bool = True
    per_detection: bool = True
    mask_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    min_mask_area_ratio: float = Field(default=0.30, ge=0.0)
    # ge=0.0 only (no upper bound of 1.0): this is now a coverage ratio
    # against the detection bbox area (see sam2.py backend), and a
    # refined mask can in principle exceed the original bbox area.


class RefinementOutputSection(BaseModel):
    """Refined bounding-box generation constraints."""

    use_mask_bbox: bool = True
    fallback_to_detection_bbox: bool = True
    min_mask_coverage_ratio: float = Field(default=0.55, ge=0.0, le=1.0)
    max_bbox_expansion_ratio: float = Field(default=1.50, ge=1.0)
    min_refinement_iou: float = Field(default=0.50, ge=0.0, le=1.0)


class RefinementSection(BaseModel):
    """Detection refinement / segmentation module configuration."""

    enabled: bool = True
    backend: str = "none"  # none | sam2
    trigger: RefinementTriggerSection = Field(default_factory=RefinementTriggerSection)
    sam2: Sam2Section = Field(default_factory=Sam2Section)
    output: RefinementOutputSection = Field(default_factory=RefinementOutputSection)

    @model_validator(mode="after")
    def validate_coverage_thresholds(self) -> "RefinementSection":
        # Both ratios are now measured against the same denominator (the
        # original detection bbox area, see sam2.py backend). Keeping the
        # backend-level filter looser than the output-level filter avoids
        # one of them being silently redundant.
        if self.sam2.min_mask_area_ratio > self.output.min_mask_coverage_ratio:
            raise ValueError(
                "refinement.sam2.min_mask_area_ratio "
                f"({self.sam2.min_mask_area_ratio}) must not exceed "
                "refinement.output.min_mask_coverage_ratio "
                f"({self.output.min_mask_coverage_ratio}); the stricter "
                "check should live in refinement.output, not the backend."
            )
        return self


class CroppingSection(BaseModel):
    """Product cropping module configuration."""

    padding_pixels: int
    target_size: list[int]
    use_refined_bbox: bool = True


class Siglip2Section(BaseModel):
    """SigLIP2 real neural retrieval backend configuration."""

    model_name: str = "google/siglip2-base-patch16-224"
    weights_path: str = ""
    device: str = "cpu"


class RetrievalSection(BaseModel):
    """Image retrieval module configuration."""

    backend: str
    embedding_dim: int
    gallery_index_path: str
    gallery_metadata_path: str
    top_k: int
    color_hist_bins: int
    build_gallery_index: bool = True
    siglip2: Siglip2Section = Field(default_factory=Siglip2Section)


class DecisionSection(BaseModel):
    """Decision engine configuration."""

    similarity_threshold: float = Field(ge=0.0, le=1.0)
    min_confidence_accept: float = Field(ge=0.0, le=1.0)
    uncertain_band: float = Field(ge=0.0, le=1.0)
    detection_weight: float = Field(ge=0.0, le=1.0)
    similarity_weight: float = Field(ge=0.0, le=1.0)
    ambiguous_top_n: int = 3
    ambiguous_margin: float = 0.05


class OcrPluginSection(BaseModel):
    """OCR plugin configuration."""

    enabled: bool
    language: str | list[str] = "en"
    device: str = "cpu"
    min_text_length: int = 3
    confidence_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    upscale_enabled: bool = True
    upscale_max: int = Field(default=3, ge=1, le=3)
    upscale_threshold_small: int = 300
    upscale_threshold_medium: int = 600
    clahe_enabled: bool = True
    clahe_clip_limit: float = Field(default=2.0, gt=0.0)
    clahe_tile_grid_size: int = Field(default=8, ge=1)
    rotation_enabled: bool = True
    rotation_angles: list[int] = Field(default_factory=lambda: [90, 180, 270])


class ColorPluginSection(BaseModel):
    """Color extraction plugin configuration."""

    enabled: bool = True

    # ROI detection & preprocessing
    roi_enabled: bool = True
    clustering_size: int = Field(default=96, ge=32, le=512)
    roi_detection_size: int = Field(default=256, ge=32, le=1024)

    # Edge detection
    blur_kernel_size: int = Field(default=3, ge=1, le=31)
    canny_threshold1: int = Field(default=30, ge=0, le=255)
    canny_threshold2: int = Field(default=100, ge=0, le=255)

    # Morphological processing
    morphology_enabled: bool = True
    morphology_kernel_size: int = Field(default=5, ge=3, le=31)

    # Rectangle candidate filtering
    min_area_ratio: float = Field(default=0.45, ge=0.0, le=1.0)
    max_area_ratio: float = Field(default=0.75, ge=0.0, le=1.0)
    target_area_ratio: float = Field(default=0.60, ge=0.0, le=1.0)
    min_rectangularity: float = Field(default=0.80, ge=0.0, le=1.0)
    min_aspect_ratio: float = Field(default=0.70, gt=0.0)
    max_aspect_ratio: float = Field(default=2.50, gt=0.0)

    # ROI refinement & fallback
    roi_shrink_ratio: float = Field(default=0.08, ge=0.0, le=0.40)
    fallback_center_roi: bool = True
    fallback_roi_width_ratio: float = Field(default=0.70, gt=0.0, le=1.0)
    fallback_roi_height_ratio: float = Field(default=0.70, gt=0.0, le=1.0)

    # Lower fallback ROI
    fallback_lower_width_ratio: float = Field(default=0.20, gt=0.0, le=1.0)
    fallback_lower_height_ratio: float = Field(default=0.20, gt=0.0, le=1.0)
    fallback_lower_x_ratio: float = Field(default=0.50, ge=0.0, le=1.0)
    fallback_lower_y_ratio: float = Field(default=0.50, ge=0.0, le=1.0)
    lower_position_target_y: float = Field(default=0.50, ge=0.0, le=1.0)
    lower_position_tolerance: float = Field(default=0.45, gt=0.0)

    # Color preprocessing (CIELAB color space)
    use_lab: bool = True
    remove_highlights: bool = True
    highlight_l_threshold: int = Field(default=245, ge=0, le=255)
    highlight_l_secondary_threshold: int = Field(default=200, ge=0, le=255)
    highlight_chroma_threshold: float = Field(default=15.0, ge=0.0)
    min_dominant_chroma: float = Field(default=10.0, ge=0.0)

    # Spatial weighting
    center_weight_enabled: bool = True
    center_weight_sigma_ratio: float = Field(default=0.6, gt=0.0)

    # K-Means clustering
    n_clusters: int = Field(default=3, ge=1, le=20)
    kmeans_max_iter: int = Field(default=20, ge=1, le=1000)
    kmeans_epsilon: float = Field(default=0.5, gt=0.0)
    kmeans_attempts: int = Field(default=3, ge=1, le=20)
    kmeans_l_weight: float = Field(default=0.9, gt=0.0, le=1.0)


class BarcodePluginSection(BaseModel):
    """Barcode decoding plugin configuration."""

    enabled: bool

    # --- confidence computation (see BarcodePlugin._compute_confidence) ---
    quality_saturation: float = Field(
        default=30.0, gt=0.0,
        description="pyzbar `quality` value at/above which quality_signal saturates to 1.0.",
    )
    type_trust_default: float = Field(
        default=0.70, ge=0.0, le=1.0,
        description="Trust applied to a decoded symbology not listed in `type_trust`.",
    )
    type_trust: dict[str, float] = Field(
        default_factory=lambda: {
            "EAN13": 1.00,
            "EAN8": 1.00,
            "UPCA": 1.00,
            "UPCE": 1.00,
            "CODE128": 1.00,
            "CODE39": 0.60,
            "CODABAR": 0.60,
            "QRCODE": 0.90,
        },
        description="Per-symbology trust weight in [0, 1], keyed by pyzbar `ZBarSymbol` name.",
    )
    ambiguity_penalty: float = Field(
        default=0.50, ge=0.0, le=1.0,
        description="Multiplier applied when a crop decodes >1 distinct barcode value.",
    )

    # --- presence pre-check (skip preprocessing tail when no barcode found) ---
    presence_check_enabled: bool = True
    presence_edge_canny_threshold1: int = Field(default=50, ge=0, le=255)
    presence_edge_canny_threshold2: int = Field(default=150, ge=0, le=255)
    presence_edge_density_min: float = Field(
        default=0.01, ge=0.0, le=1.0,
        description="Minimum fraction of edge pixels required to proceed past the cheapest gate.",
    )
    presence_sobel_ksize: int = Field(default=3, ge=1, le=31)
    presence_blur_kernel: int = Field(default=21, ge=1, le=101)
    presence_close_kernel_w: int = Field(default=21, ge=1, le=101)
    presence_close_kernel_h: int = Field(default=7, ge=1, le=101)
    presence_min_area_ratio: float = Field(
        default=0.02, ge=0.0, le=1.0,
        description="Minimum fraction of crop area the detected region must cover to count as a barcode.",
    )
    presence_roi_padding: int = Field(default=15, ge=0)

    # --- preprocessing pipeline (applied cumulatively, early-exit on success) ---
    preprocessing_enabled: bool = True

    # 1. Deskew via parallel-line detection (Hough) — fallback when
    #    presence-region detection is disabled or found no region.
    deskew_enabled: bool = True
    deskew_canny_threshold1: int = Field(default=50, ge=0, le=255)
    deskew_canny_threshold2: int = Field(default=150, ge=0, le=255)
    deskew_hough_threshold: int = Field(default=80, ge=1)
    deskew_max_angle: float = Field(default=45.0, gt=0.0, le=90.0)

    # 2. Upscale small crops
    upscale_enabled: bool = True
    upscale_threshold: int = Field(default=400, ge=1)
    upscale_max_factor: float = Field(default=3.0, ge=1.0)
    upscale_interpolation: Literal["cubic", "linear"] = "cubic"

    # 3. Contrast enhancement (CLAHE)
    clahe_enabled: bool = True
    clahe_clip_limit: float = Field(default=2.0, gt=0.0)
    clahe_tile_grid_size: int = Field(default=8, ge=1)

    # 4. Adaptive threshold
    adaptive_threshold_enabled: bool = True
    adaptive_threshold_block_size: int = Field(default=11, ge=3)
    adaptive_threshold_c: int = Field(default=2)

    # 5. Denoise
    denoise_enabled: bool = True
    denoise_strength: float = Field(default=10.0, gt=0.0)

    # 6. Sharpen (applied after upscale)
    sharpen_enabled: bool = True
    sharpen_blur_sigma: float = Field(default=3.0, gt=0.0)
    sharpen_amount: float = Field(default=1.5, gt=0.0)

    # 7. Fallback: fixed-angle rotation, last resort after all transforms above
    rotation_fallback_enabled: bool = True
    rotation_fallback_angles: list[int] = Field(default_factory=lambda: [90, 180, 270])

    @model_validator(mode="after")
    def validate_barcode_section(self) -> "BarcodePluginSection":
        for symbology, trust in self.type_trust.items():
            if not (0.0 <= trust <= 1.0):
                raise ValueError(f"type_trust['{symbology}']={trust} must be in [0, 1]")

        if self.adaptive_threshold_block_size % 2 == 0:
            raise ValueError("adaptive_threshold_block_size must be odd")

        if self.presence_close_kernel_w < 1 or self.presence_close_kernel_h < 1:
            raise ValueError("presence_close_kernel_w/h must be >= 1")

        return self

class PluginsSection(BaseModel):
    """Plugin system configuration."""

    enabled: bool
    ocr: OcrPluginSection
    color: ColorPluginSection
    barcode: BarcodePluginSection
    force_rules: dict[str, list[str]] = Field(default_factory=dict)


class RerankBarcodeSection(BaseModel):
    """Barcode evidence fusion configuration."""

    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class RerankOcrSection(BaseModel):
    """OCR evidence fusion configuration."""

    weight: float = Field(default=0.60, ge=0.0, le=1.0)
    min_text_length: int = Field(default=2, ge=1)
    fuzzy_threshold: float = Field(default=0.80, ge=0.0, le=1.0)


class RerankColorSection(BaseModel):
    """Color evidence fusion configuration."""

    enabled: bool = True
    weight: float = Field(default=0.20, ge=0.0, le=1.0)
    references_path: str = "data/metadata/product_colors.json"
    delta_e_strong: float = Field(default=5.0, gt=0.0)
    delta_e_weak: float = Field(default=20.0, gt=0.0)
    min_margin: float = Field(default=1.0, ge=0.0)
    margin_scale: float = Field(default=5.0, gt=0.0)
    l_weight: float = Field(default=0.1, gt=0.0, le=1.0)

class RerankHybridWeightsSection(BaseModel):
    """Weights used to combine retrieval consensus signals in hybrid mode."""

    count: float = Field(default=0.50, ge=0.0, le=1.0)
    weighted: float = Field(default=0.30, ge=0.0, le=1.0)
    margin: float = Field(default=0.20, ge=0.0, le=1.0)


class RerankRetrievalProtectionSection(BaseModel):
    """Adaptive protection against plugin evidence overriding strong retrieval consensus."""

    enabled: bool = True

    consensus_mode: Literal["count", "weighted", "hybrid"] = "count"

    consensus_start: float = Field(default=0.50, ge=0.0, le=1.0)
    consensus_strong: float = Field(default=0.80, ge=0.0, le=1.0)

    # Hybrid consensus weights
    hybrid_weights: RerankHybridWeightsSection = Field(
        default_factory=RerankHybridWeightsSection
    )

    margin_consensus_start: float = Field(default=0.10, ge=0.0)
    margin_consensus_strong: float = Field(default=0.30, ge=0.0)

    boost_ratio_min: float = Field(default=0.20, ge=0.0, le=1.0)
    boost_ratio_max: float = Field(default=1.00, ge=0.0, le=1.0)

    color_strong_ratio: float = Field(default=0.20, ge=0.0, le=1.0)
    ocr_strong_ratio: float = Field(default=0.30, ge=0.0, le=1.0)
    barcode_strong_ratio: float = Field(default=1.00, ge=0.0, le=1.0)

    min_switch_margin: float = Field(default=0.05, ge=0.0)
    
    @model_validator(mode="after")
    def validate_thresholds(self) -> "RerankRetrievalProtectionSection":
        if self.consensus_strong <= self.consensus_start:
            raise ValueError(
                "consensus_strong must be greater than consensus_start"
            )

        if self.margin_consensus_strong <= self.margin_consensus_start:
            raise ValueError(
                "margin_consensus_strong must be greater than "
                "margin_consensus_start"
            )

        if self.boost_ratio_max < self.boost_ratio_min:
            raise ValueError(
                "boost_ratio_max must be greater than or equal to "
                "boost_ratio_min"
            )

        return self


class RerankSection(BaseModel):
    """Reranking / evidence fusion configuration."""

    retrieval_protection: RerankRetrievalProtectionSection = Field(
        default_factory=RerankRetrievalProtectionSection
    )
    barcode: RerankBarcodeSection = Field(default_factory=RerankBarcodeSection)
    ocr: RerankOcrSection = Field(default_factory=RerankOcrSection)
    color: RerankColorSection = Field(default_factory=RerankColorSection)
    confusable_pairs: list[list[str]] = Field(default_factory=list)
    confusable_min_agreeing_plugins: int = Field(default=2, ge=1)


class StorageSection(BaseModel):
    """Storage / export configuration."""

    save_json: bool
    save_csv: bool
    save_annotated_image: bool
    json_filename: str
    csv_filename: str
    annotated_image_filename: str
    box_color: list[int]
    box_thickness: int
    font_scale: float


class ValidationStagesSection(BaseModel):
    """Per-stage enable/disable switches for VAL."""

    detection: bool = True
    cropping: bool = True
    overlap: bool = True
    segmentation: bool = True
    retrieval: bool = True
    decision: bool = True
    plugins: bool = True
    fusion: bool = True
    end_to_end: bool = True


class ValidationMetricsSection(BaseModel):
    """Per-stage list of metric names."""

    detection: list[str] = Field(default_factory=lambda: ["precision", "recall", "f1", "mean_iou"])
    cropping: list[str] = Field(default_factory=lambda: ["valid_rate", "mean_iou"])
    overlap: list[str] = Field(default_factory=lambda: ["precision", "recall", "f1"])
    segmentation: list[str] = Field(default_factory=lambda: ["iou_improvement", "improved_rate"])
    retrieval: list[str] = Field(
        default_factory=lambda: ["top1_accuracy", "topk_accuracy", "mrr", "mean_rank", "recall_at_k"]
    )
    decision: list[str] = Field(
        default_factory=lambda: ["accuracy", "precision", "recall", "f1", "confusion_matrix"]
    )
    plugins: list[str] = Field(
            default_factory=lambda: [
                "trigger_rate", "success_rate", "coverage_rate", "accuracy",
                "correction_rate", "degradation_rate", "influence_rate", "latency",
            ]
        )
    fusion: list[str] = Field(
        default_factory=lambda: ["accuracy_before", "accuracy_after", "improvement", "correction_rate"]
    )
    end_to_end: list[str] = Field(
        default_factory=lambda: ["precision", "recall", "f1", "confusion_matrix"]
    )


class ValidationSection(BaseModel):
    """Validation / benchmarking configuration."""

    iou_match_threshold: float
    top_k: int = 5
    confidence_threshold: float = 0.0
    report_json_filename: str
    report_csv_filename: str
    summary_filename: str
    records_filename: str = "records.csv"
    chart_filename: str = "validation_summary_chart.png"
    annotated_images_dirname: str = "validation_images"
    export_records: bool = True
    save_annotated_images: bool = True
    stages: ValidationStagesSection = Field(default_factory=ValidationStagesSection)
    metrics: ValidationMetricsSection = Field(default_factory=ValidationMetricsSection)


class AppConfig(BaseModel):
    """Root configuration object composed of all sub-sections."""

    app: AppSection
    logging: LoggingSection
    paths: PathsSection
    catalog: CatalogSection
    detection: DetectionSection
    refinement: RefinementSection = Field(default_factory=RefinementSection)
    cropping: CroppingSection
    retrieval: RetrievalSection
    decision: DecisionSection
    plugins: PluginsSection
    rerank: RerankSection = Field(default_factory=RerankSection)
    storage: StorageSection
    validation: ValidationSection
    project_root: str

    def resolve_path(self, relative_path: str) -> Path:
        """Resolves a config-relative path against the project root."""
        path = Path(relative_path)
        if path.is_absolute():
            return path
        return Path(self.project_root) / path


def _find_project_root(start: Path) -> Path:
    """Walks upward from `start` until a `configs/config.yaml` is found."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "configs" / "config.yaml").is_file():
            return candidate
    raise FileNotFoundError(
        f"Could not locate 'configs/config.yaml' relative to '{start}'. Ensure the project structure is intact."
    )


@functools.lru_cache(maxsize=1)
def load_config(config_path: str | None = None) -> AppConfig:
    """Loads and validates the application configuration from YAML."""
    if config_path is not None:
        resolved_path = Path(config_path).resolve()
        project_root = resolved_path.parent.parent
    else:
        project_root = _find_project_root(Path(__file__).parent)
        resolved_path = project_root / "configs" / "config.yaml"

    with open(resolved_path, "r", encoding="utf-8") as file_handle:
        raw_data: dict[str, Any] = yaml.safe_load(file_handle)

    raw_data["project_root"] = str(project_root)

    try:
        return AppConfig(**raw_data)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid configuration schema in '{resolved_path}': {exc}") from exc


def reload_config(config_path: str | None = None) -> AppConfig:
    """Forces a fresh reload of the configuration, bypassing the cache."""
    load_config.cache_clear()
    return load_config(config_path)