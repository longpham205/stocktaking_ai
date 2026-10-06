"""Unit tests for engine.core.color_signature and its use by ColorPlugin and Reranker."""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.color_signature import ColorSignatureStore, compute_signature, signature_similarity
from engine.decision.decision import DecisionEngine
from engine.decision.reranker import Reranker
from engine.models.models import CropImage, BoundingBox, PluginResult, RetrievalCandidate, RetrievalResult
from engine.plugins.color import ColorPlugin

PINK = (140, 100, 220)  # BGR
BLUE = (200, 120, 40)
WHITE = (250, 250, 250)


def _crop_image(color: tuple[int, int, int], background: tuple[int, int, int] = (128, 128, 128)) -> np.ndarray:
    """A coloured product in the middle of a grey background."""
    image = np.full((120, 120, 3), background, dtype=np.uint8)
    image[20:100, 20:100] = color
    return image


def _colour_catalog(*declared: str):
    """In-memory catalog where only the given product ids declare a colour code."""
    from engine.catalog.repository import CatalogData, InMemoryCatalogRepository, ProductRecord

    products = [ProductRecord(product_id=str(i), product_name=f"P{i}") for i in range(1, 4)]
    return InMemoryCatalogRepository(CatalogData.build(products, {pid: {"color_code": f"C{pid}"} for pid in declared}, {}))


def _signature_config(test_config, tmp_path, exemplars: dict[str, np.ndarray]):
    path = tmp_path / "color_signatures.npz"
    ColorSignatureStore(exemplars).save(path)
    color = test_config.plugins.color.model_copy(update={"mode": "signature", "signatures_path": str(path)})
    return test_config.model_copy(update={"plugins": test_config.plugins.model_copy(update={"color": color})})


def _retrieval(*pairs: tuple[str, float]) -> RetrievalResult:
    candidates = [
        RetrievalCandidate(product_id=pid, product_name=f"P{pid}", similarity_score=score, rank=rank)
        for rank, (pid, score) in enumerate(pairs, start=1)
    ]
    return RetrievalResult(crop_id="c1", candidates=candidates, detection_confidence=0.9)


def _color_result(plugin: ColorPlugin, image: np.ndarray) -> PluginResult:
    crop = CropImage(
        crop_id="c1",
        image_id="i1",
        image_array=image,
        raw_image_array=image,
        source_bbox=BoundingBox(0, 0, image.shape[1], image.shape[0]),
        detection_confidence=0.9,
    )
    return PluginResult(crop_id="c1", executed_plugins=["color"], evidence={"color": plugin.run(crop)})


def test_signature_separates_colours_and_ignores_white() -> None:
    """Different colours barely overlap; a white box has no coloured pixels at all."""
    pink, pink_fraction = compute_signature(_crop_image(PINK))
    blue, _ = compute_signature(_crop_image(BLUE))
    white, white_fraction = compute_signature(_crop_image(WHITE))

    assert pink.sum() == pytest.approx(1.0, abs=1e-4)
    assert signature_similarity(pink, np.stack([pink])) == pytest.approx(1.0, abs=1e-4)
    assert signature_similarity(pink, np.stack([blue])) < 0.1
    assert pink_fraction > 0.2
    assert white_fraction < 0.01
    assert white.sum() == 0.0


def test_darker_photo_still_matches_its_own_colour_best() -> None:
    """Under-exposure shifts the signature (saturation drops) but not towards another colour."""
    bright_pink, _ = compute_signature(_crop_image(PINK))
    blue, _ = compute_signature(_crop_image(BLUE))
    dark_pink, _ = compute_signature((_crop_image(PINK) * 0.6).astype(np.uint8))

    assert signature_similarity(dark_pink, np.stack([bright_pink])) > signature_similarity(dark_pink, np.stack([blue])) + 0.1


def test_store_uses_the_best_exemplar_not_the_average() -> None:
    """A SKU whose faces differ matches through the one face that is visible."""
    pink, _ = compute_signature(_crop_image(PINK))
    blue, _ = compute_signature(_crop_image(BLUE))
    store = ColorSignatureStore({"1": np.stack([blue, blue, blue, pink])})

    assert store.similarity(pink, "1") == pytest.approx(1.0, abs=1e-4)
    assert store.similarity(pink, "2") is None


def test_missing_signature_file_is_an_error(test_config, test_catalog, tmp_path) -> None:
    """Signature mode without the file must fail at start-up, not silently skip colour."""
    color = test_config.plugins.color.model_copy(update={"mode": "signature", "signatures_path": str(tmp_path / "none.npz")})
    config = test_config.model_copy(update={"plugins": test_config.plugins.model_copy(update={"color": color})})

    with pytest.raises(FileNotFoundError):
        Reranker(config, DecisionEngine(config, test_catalog), lambda pid: None, catalog=test_catalog)


def test_reranker_colour_signature_switches_to_the_matching_sku(test_config, tmp_path) -> None:
    """Rank 2 wins when the crop's colours match its gallery and not rank 1's."""
    pink, _ = compute_signature(_crop_image(PINK))
    blue, _ = compute_signature(_crop_image(BLUE))
    config = _signature_config(test_config, tmp_path, {"1": np.stack([blue]), "2": np.stack([pink])})
    catalog = _colour_catalog("1", "2")
    reranker = Reranker(config, DecisionEngine(config, catalog), lambda pid: None, catalog=catalog)

    result = reranker.rerank(_retrieval(("1", 0.90), ("2", 0.88)), _color_result(ColorPlugin(config), _crop_image(PINK)))

    assert result.product_id == "2"


def test_reranker_colour_signature_needs_a_declared_colour_code(test_config, tmp_path) -> None:
    """Colour is opt-in: without a color_code on the favoured SKU the crop's colour is ignored."""
    pink, _ = compute_signature(_crop_image(PINK))
    blue, _ = compute_signature(_crop_image(BLUE))
    config = _signature_config(test_config, tmp_path, {"1": np.stack([blue]), "2": np.stack([pink])})
    catalog = _colour_catalog("1")
    reranker = Reranker(config, DecisionEngine(config, catalog), lambda pid: None, catalog=catalog)

    result = reranker.rerank(_retrieval(("1", 0.90), ("2", 0.88)), _color_result(ColorPlugin(config), _crop_image(PINK)))

    assert result.product_id == "1"


def test_reranker_colour_signature_is_silent_on_a_white_box(test_config, test_catalog, tmp_path) -> None:
    """Too few coloured pixels: colour gives no evidence and rank 1 stays."""
    pink, _ = compute_signature(_crop_image(PINK))
    blue, _ = compute_signature(_crop_image(BLUE))
    config = _signature_config(test_config, tmp_path, {"1": np.stack([blue]), "2": np.stack([pink])})
    catalog = _colour_catalog("1", "2")
    reranker = Reranker(config, DecisionEngine(config, catalog), lambda pid: None, catalog=catalog)

    result = reranker.rerank(_retrieval(("1", 0.90), ("2", 0.88)), _color_result(ColorPlugin(config), _crop_image(WHITE)))

    assert result.product_id == "1"
    assert result.rerank_debug["candidates"][0]["color_boost"] == 0.0


def test_reranker_colour_signature_abstains_when_a_candidate_has_no_signature(test_config, test_catalog, tmp_path) -> None:
    """A SKU without gallery signatures cannot be compared fairly, so nobody gets colour evidence."""
    pink, _ = compute_signature(_crop_image(PINK))
    config = _signature_config(test_config, tmp_path, {"2": np.stack([pink])})
    catalog = _colour_catalog("1", "2")
    reranker = Reranker(config, DecisionEngine(config, catalog), lambda pid: None, catalog=catalog)

    result = reranker.rerank(_retrieval(("1", 0.90), ("2", 0.88)), _color_result(ColorPlugin(config), _crop_image(PINK)))

    assert result.product_id == "1"
