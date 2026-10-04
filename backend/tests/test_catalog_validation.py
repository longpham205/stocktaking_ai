"""Test luật bằng chứng catalog (engine/catalog/validation.py)."""

from __future__ import annotations

import pytest

from engine.catalog.validation import (
    CatalogValidationError,
    normalize_ocr_keywords,
    parse_hex,
    rgb_to_hex,
    validate_catalog,
)

IDS = ["5", "7", "8", "9"]


def _good():
    return {
        "5": {"force_evidence": ["barcode", "color"], "color_code": "BE203"},
        "7": {"force_evidence": ["ocr"], "ocr_keywords": ["ABA"], "confusable_with": ["8"]},
        "8": {"force_evidence": ["ocr"], "ocr_keywords": ["ABC"], "confusable_with": ["7"]},
    }


def test_valid_catalog_passes():
    rep = validate_catalog(IDS, _good(), {"BE203"}, ocr_min_length=3)
    assert rep.ok and rep.warnings == []
    rep.raise_if_errors()


def test_missing_color_reference_is_warning_only():
    rep = validate_catalog(IDS, _good(), set(), ocr_min_length=3)
    assert rep.ok
    assert any("BE203" in w for w in rep.warnings)


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda e: e["7"].update(confusable_with=["99"]), "không tồn tại"),
        (lambda e: e["7"].update(confusable_with=["7"]), "chính nó"),
        (lambda e: e["8"].pop("confusable_with"), "không hai chiều"),
        (lambda e: e["8"].update(ocr_keywords=["ABA"]), "trùng"),
        (lambda e: e["8"].pop("ocr_keywords"), "thiếu ocr_keywords"),
        (lambda e: e["5"].update(force_evidence=["sam2"]), "plugin lạ"),
        (lambda e: e["7"].update(ocr_keywords=["aba"]), "chưa chuẩn hoá"),
        (lambda e: e["7"].update(ocr_keywords=["AB"]), "ngắn hơn"),
        (lambda e: e["5"].update(color_code=""), "không rỗng"),
        (lambda e: e.update({"42": {"color_code": "X"}}), "không tồn tại"),
    ],
)
def test_hard_errors(mutate, needle):
    ev = _good()
    mutate(ev)
    rep = validate_catalog(IDS, ev, {"BE203"}, ocr_min_length=3)
    assert not rep.ok
    assert any(needle in e for e in rep.errors), rep.errors
    with pytest.raises(CatalogValidationError):
        rep.raise_if_errors()


def test_confusable_pair_without_ocr_needs_no_keywords():
    ev = {"7": {"confusable_with": ["8"]}, "8": {"confusable_with": ["7"]}}
    assert validate_catalog(IDS, ev, set(), ocr_min_length=3).ok


def test_unknown_evidence_type_is_warning():
    rep = validate_catalog(IDS, {"9": {"shape": [1]}}, set(), ocr_min_length=3)
    assert rep.ok and rep.warnings


def test_normalize_ocr_keywords():
    assert normalize_ocr_keywords([" be203 ", "BE203", "aba", ""], 3) == ["BE203", "ABA"]
    with pytest.raises(ValueError):
        normalize_ocr_keywords(["AB"], 3)
    with pytest.raises(ValueError):
        normalize_ocr_keywords([12], 1)


def test_hex_helpers():
    assert parse_hex("#C7A194") == (199, 161, 148)
    assert parse_hex("c7a194") == (199, 161, 148)
    assert rgb_to_hex(199, 161, 148) == "#C7A194"
    with pytest.raises(ValueError):
        parse_hex("#XYZ")
    with pytest.raises(ValueError):
        rgb_to_hex(256, 0, 0)


def test_keyword_normalized_like_reranker():
    from engine.catalog.validation import normalize_ocr_token

    assert normalize_ocr_keywords(["be-203", "100 g", "BE203"], 3) == ["BE203", "100G"]
    assert normalize_ocr_token("A/B c-1") == "ABC1"
