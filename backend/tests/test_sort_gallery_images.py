"""Test scripts/sort_gallery_images.py (phần logic; cửa sổ tkinter không mở trong test)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("sort_gallery_images", ROOT / "scripts" / "sort_gallery_images.py")
sg = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = sg  # dataclass cần module có trong sys.modules
_SPEC.loader.exec_module(sg)


def _source(tmp_path: Path, names: list[str]) -> Path:
    source = tmp_path / "nguon"
    source.mkdir()
    for name in names:
        (source / name).write_bytes(name.encode())
    return source


def test_images_are_numbered_per_folder_and_the_source_is_kept(tmp_path):
    source = _source(tmp_path, ["b.JPG", "a.jpg", "c.png", "ghi_chu.txt"])
    dest = tmp_path / "inbox"
    sorter = sg.Sorter(source, dest)
    assert [p.name for p in sorter.pending] == ["a.jpg", "b.JPG", "c.png"]  # theo tên, bỏ file không phải ảnh

    assert sorter.assign("Kem A").name == "0001.jpg"
    assert sorter.assign(" Kem A ").name == "0002.jpg"  # đuôi viết thường, tên thư mục được cắt khoảng trắng
    assert sorter.assign("Son B").name == "0001.png"
    assert sorter.current is None
    assert (dest / "Kem A" / "0002.jpg").read_bytes() == b"b.JPG"
    assert sorted(p.name for p in source.iterdir()) == ["_sort_log.json", "a.jpg", "b.JPG", "c.png", "ghi_chu.txt"]
    assert sorter.folders() == ["Kem A", "Son B"] and sorter.count("Kem A") == 2


def test_numbering_continues_after_existing_numbered_images(tmp_path):
    source = _source(tmp_path, ["x.jpg"])
    folder = tmp_path / "gallery" / "0029"
    folder.mkdir(parents=True)
    (folder / "0007.jpg").write_bytes(b"old")
    (folder / "IMG_2779.HEIC.png").write_bytes(b"old")  # tên không phải số: không tính
    assert sg.Sorter(source, tmp_path / "gallery").assign("0029").name == "0008.jpg"


def test_a_second_run_resumes_where_the_first_stopped(tmp_path):
    source = _source(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    dest = tmp_path / "inbox"
    first = sg.Sorter(source, dest)
    first.assign("A")
    first.skip()

    second = sg.Sorter(source, dest)
    assert [p.name for p in second.pending] == ["c.jpg"] and second.total == 1


def test_undo_removes_the_copy_and_the_folder_it_created(tmp_path):
    source = _source(tmp_path, ["a.jpg", "b.jpg"])
    dest = tmp_path / "inbox"
    sorter = sg.Sorter(source, dest)
    sorter.assign("A")
    sorter.skip()

    assert sorter.undo().name == "b.jpg"  # bỏ qua được hoàn tác trước
    assert sorter.undo().name == "a.jpg"
    assert not (dest / "A").exists()
    assert [p.name for p in sorter.pending] == ["a.jpg", "b.jpg"]
    assert sg.load_log(source) == {}
    assert sorter.undo() is None


def test_move_takes_the_image_out_of_the_source_and_undo_puts_it_back(tmp_path):
    source = _source(tmp_path, ["a.jpg"])
    sorter = sg.Sorter(source, tmp_path / "inbox", move=True)
    target = sorter.assign("A")
    assert target.is_file() and not (source / "a.jpg").exists()
    sorter.undo()
    assert (source / "a.jpg").read_bytes() == b"a.jpg" and not target.exists()


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a\\b", "..", "_an", "ten?", "x" * 81])
def test_unusable_folder_names_are_refused_and_nothing_is_copied(tmp_path, name):
    source = _source(tmp_path, ["a.jpg"])
    sorter = sg.Sorter(source, tmp_path / "inbox")
    with pytest.raises(ValueError):
        sorter.assign(name)
    assert sorter.current is not None and not (tmp_path / "inbox").exists()


def test_japanese_folder_names_are_allowed(tmp_path):
    source = _source(tmp_path, ["a.jpg"])
    name = "マキアージュ　スポンジパフ"
    assert sg.Sorter(source, tmp_path / "inbox").assign(name).parent.name == name
