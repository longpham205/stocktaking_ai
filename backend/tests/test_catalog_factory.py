"""Test chọn nguồn catalog theo config (engine/catalog/factory.py)."""

from __future__ import annotations

import pytest

from engine.catalog.factory import open_catalog_repository
from engine.catalog.migrate import LegacySources, apply_plan, build_plan
from engine.catalog.db import make_engine
from engine.catalog.repository import DatabaseCatalogRepository, SqliteCatalogRepository
from engine.catalog.snapshot import JsonSnapshotCatalogRepository, write_snapshot
from engine.core.config import AppConfig, build_config


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


def test_database_source_reads_the_same_catalog_through_a_url(test_config, tmp_path):
    """Nguồn `database` (URL) và nguồn `sqlite` (đường dẫn) đọc cùng một DB phải cho cùng nội dung."""
    src = LegacySources(
        products=[{"product_id": "1", "product_name": "A", "folder": "A", "barcode": "8930000000017"},
                  {"product_id": "2", "product_name": "B", "folder": "B"}],
        product_ids={"1": "A", "2": "B"}, product_ids_next=3, colors={},
    )
    db = tmp_path / "cat.db"
    eng = make_engine(db)
    apply_plan(eng, build_plan(src))
    eng.dispose()
    by_path = open_catalog_repository(_with_catalog(test_config, source="sqlite", db_path=str(db)))
    by_url = open_catalog_repository(
        _with_catalog(test_config, source="database", db_url=f"sqlite:///{db.as_posix()}", db_path=None)
    )
    assert isinstance(by_url, DatabaseCatalogRepository)
    assert by_url.version() == by_path.version()
    assert by_url.folder_to_product_id() == {"A": "1", "B": "2"}


def test_config_database_source_rules(test_config):
    section = type(test_config.catalog)
    ok = section(source="database", db_url="postgresql+psycopg://u:p@127.0.0.1:5437/db")
    assert ok.db_path is None
    with pytest.raises(ValueError, match="db_url"):
        section(source="database")  # thiếu URL
    with pytest.raises(ValueError, match="db_url"):
        section(source="sqlite", db_path="data/db/app.db", db_url="sqlite:///x.db")  # URL sai nguồn
    with pytest.raises(ValueError, match="db_url"):
        section(source="database", db_url="sqlite:///x.db", db_path="data/db/app.db")  # khai báo thừa


def test_build_config_catalog_db_url_replaces_the_catalog_section(test_config):
    test_config_path = test_config.resolve_path("configs/config.yaml")
    plain = build_config(test_config_path)
    assert plain.catalog.source == "sqlite" and plain.catalog.db_url is None
    cfg = build_config(test_config_path, catalog_db_url="postgresql+psycopg://u:p@127.0.0.1:5437/db")
    assert (cfg.catalog.source, cfg.catalog.db_path) == ("database", None)
    assert cfg.catalog.db_url == "postgresql+psycopg://u:p@127.0.0.1:5437/db"
    assert cfg.model_dump(exclude={"catalog"}) == plain.model_dump(exclude={"catalog"})

