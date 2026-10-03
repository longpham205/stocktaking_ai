"""P1-4: `InferenceRunner.run_single` — `persist`, chuyển tiếp `min_confidence_accept`."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from engine.inference.infer import InferenceRunner


def _write_image(tmp_path: Path, arr: np.ndarray) -> str:
    path = tmp_path / "query.jpg"
    assert cv2.imwrite(str(path), arr)
    return str(path)


def test_run_single_persists_by_default(gallery_config, synthetic_image, tmp_path, monkeypatch) -> None:
    runner = InferenceRunner(gallery_config)
    calls: list[int] = []
    monkeypatch.setattr(runner._storage, "save_all", lambda *a, **k: calls.append(1))

    runner.run_single(_write_image(tmp_path, synthetic_image))

    assert calls == [1]  # hành vi cũ không đổi


def test_run_single_persist_false_skips_storage(gallery_config, synthetic_image, tmp_path, monkeypatch) -> None:
    runner = InferenceRunner(gallery_config)
    calls: list[int] = []
    monkeypatch.setattr(runner._storage, "save_all", lambda *a, **k: calls.append(1))

    result = runner.run_single(_write_image(tmp_path, synthetic_image), persist=False)

    assert calls == []
    assert result.image_id  # vẫn trả kết quả bình thường


def test_run_single_forwards_both_thresholds(gallery_config, synthetic_image, tmp_path, monkeypatch) -> None:
    runner = InferenceRunner(gallery_config)
    seen: dict = {}
    real_run = runner._pipeline.run

    def spy(image_data, similarity_threshold=None, min_confidence_accept=None):
        seen["args"] = (similarity_threshold, min_confidence_accept)
        return real_run(image_data, similarity_threshold=similarity_threshold, min_confidence_accept=min_confidence_accept)

    monkeypatch.setattr(runner._pipeline, "run", spy)
    runner.run_single(_write_image(tmp_path, synthetic_image), similarity_threshold=0.66, min_confidence_accept=0.42, persist=False)

    assert seen["args"] == (0.66, 0.42)


def test_positional_threshold_still_supported(gallery_config, synthetic_image, tmp_path, monkeypatch) -> None:
    runner = InferenceRunner(gallery_config)
    seen: dict = {}
    real_run = runner._pipeline.run

    def spy(image_data, similarity_threshold=None, min_confidence_accept=None):
        seen["args"] = (similarity_threshold, min_confidence_accept)
        return real_run(image_data, similarity_threshold=similarity_threshold, min_confidence_accept=min_confidence_accept)

    monkeypatch.setattr(runner._pipeline, "run", spy)
    runner.run_single(_write_image(tmp_path, synthetic_image), 0.8, persist=False)  # cách gọi cũ theo vị trí

    assert seen["args"] == (0.8, None)
