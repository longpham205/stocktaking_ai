"""Unit tests for engine.decision.reranker.Reranker."""

from __future__ import annotations

from engine.decision.decision import DecisionEngine
from engine.decision.reranker import Reranker
from engine.models.models import PluginResult, RetrievalCandidate, RetrievalResult


def _ocr_catalog(keywords: dict[str, list[str]]):
    """Catalog trong bộ nhớ chỉ có ``ocr_keywords`` khai báo."""
    from engine.catalog.repository import CatalogData, InMemoryCatalogRepository, ProductRecord

    products = [ProductRecord(product_id=str(i), product_name=f"P{i}") for i in range(1, 10)]
    return InMemoryCatalogRepository(CatalogData.build(products, {k: {"ocr_keywords": v} for k, v in keywords.items()}, {}))


def _catalog(entries: dict[str, dict]):
    """Builds a product_lookup callable from a plain dict."""
    return lambda product_id: entries.get(product_id)


def test_reranker_barcode_match_overrides_lower_rank(test_config, test_catalog) -> None:
    """A rank-2 candidate with a matching barcode must outrank a higher-similarity rank-1."""
    engine = DecisionEngine(test_config, test_catalog)
    catalog = _catalog(
        {
            "1": {"product_id": "1", "product_name": "A", "barcode": ""},
            "2": {"product_id": "2", "product_name": "B", "barcode": "999888777"},
        }
    )
    reranker = Reranker(test_config, engine, catalog, catalog=test_catalog)

    retrieval_result = RetrievalResult(
        crop_id="c1",
        candidates=[
            RetrievalCandidate(product_id="1", product_name="A", similarity_score=0.90, rank=1),
            RetrievalCandidate(product_id="2", product_name="B", similarity_score=0.85, rank=2),
        ],
        detection_confidence=0.8,
    )
    plugin_result = PluginResult(
        crop_id="c1",
        executed_plugins=["barcode"],
        evidence={
            "barcode": {
                "barcodes": [
                    {
                        "data": "999888777",
                        "type": "EAN13",
                        "quality": 100,
                    }
                ],
                "confidence": 1.0,
                "preprocessing_stage": "raw",
                "latency_ms": 0.0,
            }
        },
    )

    final = reranker.rerank(retrieval_result, plugin_result)

    assert final.product_id == "2"
    assert final.needs_plugin is False


def test_reranker_no_evidence_keeps_original_winner(test_config, test_catalog) -> None:
    """With no usable evidence, the highest-similarity candidate must still win."""
    engine = DecisionEngine(test_config, test_catalog)
    catalog = _catalog({"1": {"product_id": "1", "product_name": "A", "barcode": ""}})
    reranker = Reranker(test_config, engine, catalog, catalog=test_catalog)

    retrieval_result = RetrievalResult(
        crop_id="c1",
        candidates=[RetrievalCandidate(product_id="1", product_name="A", similarity_score=0.90, rank=1)],
        detection_confidence=0.8,
    )
    plugin_result = PluginResult(crop_id="c1", executed_plugins=[], evidence={})

    final = reranker.rerank(retrieval_result, plugin_result)

    assert final.product_id == "1"


def test_reranker_handles_empty_candidates(test_config, test_catalog) -> None:
    """Reranker must gracefully reject when there are no candidates at all."""
    engine = DecisionEngine(test_config, test_catalog)
    reranker = Reranker(test_config, engine, _catalog({}), catalog=test_catalog)

    retrieval_result = RetrievalResult(crop_id="c1", candidates=[], detection_confidence=0.5)
    plugin_result = PluginResult(crop_id="c1")

    final = reranker.rerank(retrieval_result, plugin_result)

    assert final.status == "rejected"
    assert final.product_id is None


def test_reranker_ocr_fuzzy_match_boosts_correct_candidate(test_config, test_catalog) -> None:
    """OCR text closely matching a candidate's catalog name should favor it."""
    engine = DecisionEngine(test_config, test_catalog)
    catalog = _catalog(
        {
            "1": {"product_id": "1", "product_name": "Cola Can", "barcode": ""},
            "2": {"product_id": "2", "product_name": "Milk Carton", "barcode": ""},
        }
    )
    reranker = Reranker(test_config, engine, catalog, catalog=_ocr_catalog({"1": ["COLA"], "2": ["MILK", "CARTON"]}))

    retrieval_result = RetrievalResult(
        crop_id="c1",
        candidates=[
            RetrievalCandidate(product_id="1", product_name="Cola Can", similarity_score=0.70, rank=1),
            RetrievalCandidate(product_id="2", product_name="Milk Carton", similarity_score=0.69, rank=2),
        ],
        detection_confidence=0.8,
    )
    plugin_result = PluginResult(
        crop_id="c1",
        executed_plugins=["ocr"],
        evidence={"ocr": {"text": "Milk Carton", "text_length": 11, "confidence": 1.0}},
    )

    final = reranker.rerank(retrieval_result, plugin_result)

    assert final.product_id == "2"


def test_reranker_never_matches_ocr_against_product_name(test_config, test_catalog) -> None:
    """C8: không khai báo ocr_keywords thì OCR không cộng điểm, dù chữ đọc được trùng tên sản phẩm."""
    engine = DecisionEngine(test_config, test_catalog)
    lookup = _catalog(
        {
            "1": {"product_id": "1", "product_name": "Cola Can", "barcode": ""},
            "2": {"product_id": "2", "product_name": "Milk Carton", "barcode": ""},
        }
    )
    reranker = Reranker(test_config, engine, lookup, catalog=_ocr_catalog({}))
    retrieval_result = RetrievalResult(
        crop_id="c1",
        candidates=[
            RetrievalCandidate(product_id="1", product_name="Cola Can", similarity_score=0.70, rank=1),
            RetrievalCandidate(product_id="2", product_name="Milk Carton", similarity_score=0.69, rank=2),
        ],
        detection_confidence=0.8,
    )
    plugin_result = PluginResult(
        crop_id="c1",
        executed_plugins=["ocr"],
        evidence={"ocr": {"text": "Milk Carton", "text_length": 11, "confidence": 1.0}},
    )
    assert reranker.rerank(retrieval_result, plugin_result).product_id == "1"
