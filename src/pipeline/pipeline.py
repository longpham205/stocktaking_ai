"""Central inventory pipeline orchestrator.

`InventoryPipeline` is the single, mandatory execution path for every
workflow (Inference, Batch, Validation, UI). No module is allowed to
bypass it (see 01_PROJECT_CONTEXT.md, Section 3.1).

Runtime execution order:

    Detect
        v
    Overlap Analysis (OverlapResolver)
        v
    Segmentation Refinement (Refiner, only when OverlapResolver requests it)
        v
    Crop (uses refined bbox when available and cropping.use_refined_bbox)
        v
    For each crop:
        Retrieve (Top-K)
            v
        Decide (DecisionEngine.decide — preliminary; determines needs_plugin)
            v
        Plugins (PluginManager, only when needs_plugin)
            v
        Reranker.rerank (fuses evidence across the full Top-K -> FINAL decision)
        v
    InventoryResult

This module contains no concrete AI model implementations, performs no
file I/O, and renders no UI. Concrete detection, segmentation, retrieval,
decision, and plugin models remain isolated behind their respective
module abstractions.
"""

from __future__ import annotations

import datetime as _datetime

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.decision.decision import DecisionEngine
from src.decision.reranker import Reranker
from src.detection.cropper import Cropper
from src.detection.detector import Detector
from src.models.models import (
    CropTrace,
    ImageData,
    InventoryItem,
    InventoryResult,
    PipelineTrace,
    RefinementResult,
)
from src.pipeline.overlap import OverlapResolver
from src.plugins.manager import PluginManager
from src.retrieval.retriever import Retriever
from src.segmentation.refiner import Refiner

logger = get_logger(__name__)


class InventoryPipeline:
    """Orchestrates the complete runtime inventory pipeline."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes every pipeline component exactly once.

        Args:
            config: Fully validated application configuration.
        """
        self._config = config

        self._detector = Detector(config)
        self._overlap_resolver = OverlapResolver(config)
        self._refiner = Refiner(config)
        self._cropper = Cropper(config)
        self._retriever = Retriever(config)
        self._decision_engine = DecisionEngine(config)
        self._plugin_manager = PluginManager(config)
        self._reranker = Reranker(config, self._decision_engine, self._retriever.get_product)

        logger.info("InventoryPipeline fully initialized; all components loaded once.")

    def run(self, image_data: ImageData, similarity_threshold: float | None = None) -> InventoryResult:
        """Executes the complete inventory pipeline for one image.

        Args:
            image_data: Source image to process.
            similarity_threshold: Optional per-call override of
                `decision.similarity_threshold` (e.g. from a UI slider).
                Building an override DecisionEngine/Reranker per call is
                cheap (neither holds AI model weights — only the shared,
                already-loaded Detector/Retriever/plugins are reused), so
                this does not violate the "never reload model weights per
                query" rule (03_DEVELOPMENT_RULES.md, Rule 23). None uses
                the configured default.

        Returns:
            The consolidated InventoryResult for the processed image.
        """
        with timer() as elapsed:
            decision_engine, reranker = self._resolve_decision_components(similarity_threshold)
            detection_result, _overlap_result, _refinement_result, items = self._run_stages(
                image_data, decision_engine=decision_engine, reranker=reranker
            )

        result = InventoryResult(
            image_id=image_data.image_id,
            source_path=image_data.source_path,
            items=items,
            total_items=len(items),
            processing_time_ms=elapsed["elapsed_ms"],
            timestamp=_datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        )

        logger.info(
            "InventoryPipeline.run completed for image_id='%s': %d item(s) in %.2f ms",
            image_data.image_id,
            result.total_items,
            result.processing_time_ms,
        )
        return result

    def run_with_trace(self, image_data: ImageData) -> tuple[InventoryResult, PipelineTrace]:
        """Executes the pipeline and also returns every intermediate stage result.

        Used by VAL (`ValidationRunner`) to evaluate every stage from a
        single real pipeline execution, per the VAL spec's Core Rule
        ("VAL must measure the actual production pipeline"). Not used on
        the hot inference path (`run()`) to avoid unnecessary bookkeeping
        overhead there.

        Args:
            image_data: Source image to process.

        Returns:
            Tuple of (InventoryResult, PipelineTrace).
        """
        with timer() as elapsed:
            detection_result, overlap_result, refinement_result, items, crop_traces = self._run_stages(
                image_data, collect_trace=True
            )

        result = InventoryResult(
            image_id=image_data.image_id,
            source_path=image_data.source_path,
            items=items,
            total_items=len(items),
            processing_time_ms=elapsed["elapsed_ms"],
            timestamp=_datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        )
        trace = PipelineTrace(
            image_id=image_data.image_id,
            detection_result=detection_result,
            overlap_result=overlap_result,
            refinement_result=refinement_result,
            crops=crop_traces,
        )
        return result, trace

    def _resolve_decision_components(
        self, similarity_threshold: float | None
    ) -> tuple[DecisionEngine, Reranker]:
        """Resolves which DecisionEngine/Reranker pair to use for a run.

        Args:
            similarity_threshold: Optional per-call threshold override.

        Returns:
            The pipeline's shared (DecisionEngine, Reranker) when no
            override is requested, otherwise a freshly built lightweight
            pair using an overridden config copy.
        """
        if similarity_threshold is None:
            return self._decision_engine, self._reranker

        overridden_decision = self._config.decision.model_copy(
            update={"similarity_threshold": similarity_threshold}
        )
        overridden_config = self._config.model_copy(update={"decision": overridden_decision})
        decision_engine = DecisionEngine(overridden_config)
        reranker = Reranker(overridden_config, decision_engine, self._retriever.get_product)
        return decision_engine, reranker

    def _run_stages(
        self,
        image_data: ImageData,
        decision_engine: DecisionEngine | None = None,
        reranker: Reranker | None = None,
        collect_trace: bool = False,
    ):
        """Runs Detect -> Overlap -> Refine -> Crop -> per-crop stages.

        Args:
            image_data: Source image to process.
            decision_engine: DecisionEngine to use (defaults to the
                pipeline's shared instance).
            reranker: Reranker to use (defaults to the pipeline's shared
                instance).
            collect_trace: When True, also returns a list of CropTrace
                entries (one per crop) alongside the final items.

        Returns:
            When `collect_trace` is False:
                (detection_result, overlap_result, refinement_result, items)
            When `collect_trace` is True:
                (detection_result, overlap_result, refinement_result, items, crop_traces)
        """
        decision_engine = decision_engine or self._decision_engine
        reranker = reranker or self._reranker

        detection_result = self._detector.detect(image_data)
        overlap_result = self._overlap_resolver.resolve(detection_result)
        refinement_result = self._refine(image_data, detection_result, overlap_result)

        crops = self._cropper.crop(image_data, detection_result, refinement_result)

        items: list[InventoryItem] = []
        crop_traces: list[CropTrace] = []

        for crop in crops:
            retrieval_result = self._retriever.retrieve(crop)
            decision = decision_engine.decide(retrieval_result)

            plugin_result = None
            final_decision = decision

            if decision.needs_plugin:
                plugin_result = self._plugin_manager.run_plugins(crop, decision)
                final_decision = reranker.rerank(retrieval_result, plugin_result)

            if collect_trace:
                crop_traces.append(
                    CropTrace(
                        crop=crop,
                        retrieval_result=retrieval_result,
                        decision_result=decision,
                        plugin_result=plugin_result,
                        final_decision=final_decision,
                    )
                )

            if final_decision.status == "rejected" or final_decision.product_id is None:
                continue

            items.append(
                InventoryItem(
                    product_id=final_decision.product_id,
                    product_name=final_decision.product_name or "unknown",
                    bbox=crop.source_bbox,
                    detection_confidence=final_decision.detection_confidence,
                    similarity_score=final_decision.similarity_score,
                    final_confidence=final_decision.final_confidence,
                    status=final_decision.status,
                    plugin_evidence=plugin_result.evidence if plugin_result else {},
                )
            )

        if collect_trace:
            return detection_result, overlap_result, refinement_result, items, crop_traces
        return detection_result, overlap_result, refinement_result, items

    def _refine(self, image_data: ImageData, detection_result, overlap_result) -> RefinementResult:
        """Runs segmentation refinement when the overlap stage requests it.

        Args:
            image_data: Original source image.
            detection_result: Detector output.
            overlap_result: OverlapResolver output.

        Returns:
            A RefinementResult (may be untriggered/empty).
        """
        if not overlap_result.needs_refinement:
            return RefinementResult(image_id=image_data.image_id, triggered=False, backend=self._config.refinement.backend)

        return self._refiner.refine(image_data.image_array, detection_result, overlap_result)
