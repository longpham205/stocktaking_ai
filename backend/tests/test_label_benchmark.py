"""Test scripts/label_benchmark.py (trạng thái + xuất COCO; không chạy pipeline, không mở cửa sổ)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("label_benchmark", ROOT / "scripts" / "label_benchmark.py")
lb = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = lb
_SPEC.loader.exec_module(lb)


def _labels() -> dict:
    return {
        "products": {"7": "Kem A", "29": "Chưa đặt tên (CLEO_BLUE)"},
        "images": [
            {
                "file_name": "0001.jpg",
                "width": 1000,
                "height": 800,
                "reviewed": True,
                "touched": False,
                "boxes": [
                    {"bbox": [10.0, 20.0, 110.0, 220.0], "product_id": "29", "source": "accepted", "confidence": 0.9},
                    {"bbox": [300.0, 300.0, 400.5, 380.0], "product_id": "7", "source": "manual", "confidence": None},
                ],
            },
            {
                "file_name": "0002.jpg",
                "width": 1000,
                "height": 800,
                "reviewed": False,
                "touched": False,
                "boxes": [{"bbox": [5.0, 5.0, 50.0, 50.0], "product_id": None, "source": "rejected", "confidence": None}],
            },
        ],
    }


def test_coco_has_only_reviewed_images_in_the_benchmark_format():
    coco = lb.to_coco(_labels())
    assert [im["file_name"] for im in coco["images"]] == ["0001.jpg"]
    assert coco["images"][0] == {"id": 0, "file_name": "0001.jpg", "width": 1000, "height": 800}
    first, second = coco["annotations"]
    assert (first["image_id"], first["category_id"], first["bbox"], first["area"]) == (0, 29, [10.0, 20.0, 100.0, 200.0], 20000.0)
    assert (second["category_id"], second["bbox"]) == (7, [300.0, 300.0, 100.5, 80.0])  # x, y, rộng, cao
    assert [c["id"] for c in coco["categories"]] == [7, 29]


def test_the_validation_loader_reads_what_the_tool_writes(tmp_path):
    """Đúng các khoá mà engine.validation đọc: images[id, file_name], annotations[image_id, category_id, bbox]."""
    lb.save_labels(tmp_path, _labels())
    coco = json.loads((tmp_path / lb.COCO_NAME).read_text(encoding="utf-8"))
    assert set(coco) >= {"images", "annotations", "categories"}
    assert all({"id", "file_name"} <= set(im) for im in coco["images"])
    assert all({"image_id", "category_id", "bbox"} <= set(a) for a in coco["annotations"])
    assert lb.load_labels(tmp_path) == _labels()  # trạng thái làm việc đọc lại y nguyên


def test_an_image_with_an_unlabeled_box_cannot_be_approved():
    image = _labels()["images"][1]
    with pytest.raises(ValueError, match="1 khung chưa có sản phẩm"):
        lb.mark_reviewed(image)
    assert image["reviewed"] is False
    image["boxes"][0]["product_id"] = "7"
    lb.mark_reviewed(image)
    assert image["reviewed"] is True


def test_an_image_without_any_object_can_be_approved():
    image = {"file_name": "x.jpg", "width": 10, "height": 10, "reviewed": False, "boxes": []}
    lb.mark_reviewed(image)
    assert lb.to_coco({"products": {}, "images": [image]})["annotations"] == []


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        ([50, 60, 10, 20], [10.0, 20.0, 50.0, 60.0]),  # kéo ngược chiều
        ([-30, -5, 40, 900], [0.0, 0.0, 40.0, 800.0]),  # tràn ra ngoài ảnh
        ([10, 10, 14, 300], None),  # quá hẹp: bấm nhầm
    ],
)
def test_boxes_are_ordered_and_kept_inside_the_image(box, expected):
    assert lb.clamp_box(box, 1000, 800) == expected


def test_review_needs_the_propose_step_first(tmp_path):
    with pytest.raises(ValueError, match="propose"):
        lb.load_labels(tmp_path)


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ((200, 150), "move"),  # bên trong
        ((101, 150), "w"),
        ((300, 52), "ne"),  # góc: hai cạnh cùng lúc
        ((200, 304), "s"),  # ngay ngoài viền vẫn bắt được
        ((320, 150), None),  # ngoài hẳn
    ],
)
def test_the_part_of_a_box_under_the_pointer(point, expected):
    assert lb.grab_handle([100.0, 50.0, 300.0, 300.0], *point, tolerance=5) == expected


@pytest.mark.parametrize(
    ("handle", "delta", "expected"),
    [
        ("move", (30, -20), [130.0, 30.0, 330.0, 280.0]),
        ("move", (900, -900), [800.0, 0.0, 1000.0, 250.0]),  # dời tới mép ảnh thì dừng, giữ kích thước
        ("e", (50, 999), [100.0, 50.0, 350.0, 300.0]),  # kéo cạnh phải: chỉ x2 đổi
        ("nw", (-20, 10), [80.0, 60.0, 300.0, 300.0]),
        ("w", (500, 0), [300.0 - lb.MIN_BOX, 50.0, 300.0, 300.0]),  # không kéo lật qua cạnh đối diện
        ("s", (0, 5000), [100.0, 50.0, 300.0, 800.0]),
    ],
)
def test_dragging_moves_or_resizes_a_box_inside_the_image(handle, delta, expected):
    assert lb.drag_box([100.0, 50.0, 300.0, 300.0], handle, *delta, width=1000, height=800) == expected


def test_only_takes_ranges_and_single_ids():
    assert lb.parse_only("21, 24,29-31") == {"21", "24", "29", "30", "31"}
    with pytest.raises(ValueError, match="--only"):
        lb.parse_only("21-x")


def test_a_box_of_a_product_outside_only_is_shown_as_unsure():
    box = {"bbox": [0, 0, 9, 9], "product_id": "7", "source": "accepted"}
    assert lb.box_kind(box) == "ok"
    assert lb.box_kind(box, {"21"}) == "unsure"


def test_a_box_counts_as_edited_once_a_person_touched_it():
    assert not lb.was_edited({"bbox": [0, 0, 9, 9], "product_id": "7", "source": "accepted"})
    assert lb.was_edited({"bbox": [0, 0, 9, 9], "product_id": "7", "source": "manual"})  # gán nhãn hoặc tự vẽ
    assert lb.was_edited({"bbox": [0, 0, 9, 9], "product_id": "7", "source": "accepted", "edited": True})  # dời / đổi cỡ
