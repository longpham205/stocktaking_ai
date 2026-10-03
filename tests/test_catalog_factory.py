"""Test chọn nguồn catalog theo config (src/catalog/factory.py)."""

from __future__ import annotations

import pytest

from src.catalog.factory import open_catalog_repository
from src.catalog.migrate import LegacySources, apply_plan, build_plan
from src.catalog.db import make_engine
from src.catalog.repository import SqliteCatalogRepository
from src.catalog.snapshot import JsonSnapshotCatalogRepository, write_snapshot
from src.core.config import AppConfig


def _with_catalog(cfg: AppConfig, **update) -> AppConfig:
    return cfg.model_copy(update={"catalog": cfg.catalog.model_copy(update=update)})


def test_sqlite_and_snapshot_sources(test_config, tmp_path):
    src = LegacySources(
        products=[{"product_id": "1", "product_name": "A", "folder": "A"}],
        product_ids={"1": "A"}, product_ids_next=2, colors={},
    )
    db = tmp_path / "cat.db"
    eng = make_engine(db)
    apply_plan(eng, build_plan(src))
    eng.dispose()
    repo = open_catalog_repository(_with_catalog(test_config, source="sqlite", db_path=str(db)))
    assert isinstance(repo, SqliteCatalogRepository) and list(repo.products()) == ["1"]
    snap = write_snapshot(repo.data, tmp_path / "s.json")
    repo2 = open_catalog_repository(_with_catalog(test_config, source="snapshot", snapshot_path=str(snap)))
    assert isinstance(repo2, JsonSnapshotCatalogRepository) and repo2.version() == repo.version()


@pytest.mark.parametrize("source", ["sqlite", "snapshot"])
def test_config_requires_path_for_source(test_config, source):
    raw = test_config.catalog.model_dump()
    raw.update(source=source, db_path=None, snapshot_path=None)
    with pytest.raises(ValueError):
        type(test_config.catalog)(**raw)


def test_default_test_config_uses_seeded_sqlite(test_config):
    repo = open_catalog_repository(test_config)
    assert isinstance(repo, SqliteCatalogRepository)
    assert repo.folder_to_product_id() == {"prod_red_square": "1", "prod_blue_square": "2"}
    assert repo.force_evidence("2") == frozenset({"barcode"})


@pytest.mark.parametrize(
    "section, key",
    [("catalog", "id_mapping"), ("plugins", "force_rules"), ("rerank", "confusable_pairs")],
)
def test_removed_legacy_keys_raise_clear_error(test_config, section, key):
    raw = test_config.model_dump()
    raw[section][key] = {} if key != "confusable_pairs" else []
    with pytest.raises(ValueError, match=key):
        AppConfig(**raw)
