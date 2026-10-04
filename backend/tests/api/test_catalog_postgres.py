"""The engine's catalog on the web's Postgres: the tables Alembic builds are the engine's tables,
and the engine reads the same catalog from them as from a SQLite file."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.db import sync_database_url
from engine.catalog.db import Product, make_engine, make_engine_from_url
from engine.catalog.migrate import LegacySources, apply_plan, build_plan
from engine.catalog.repository import DatabaseCatalogRepository, SqliteCatalogRepository
from engine.catalog.sync_gallery import sync_gallery

CATALOG_TABLES = "product_evidence, product, color_reference, catalog_meta"


def test_sync_database_url_swaps_only_the_driver() -> None:
    url = "postgresql+asyncpg://u:p@127.0.0.1:5437/stocktaking"
    assert sync_database_url(url) == "postgresql+psycopg://u:p@127.0.0.1:5437/stocktaking"


@pytest.fixture
def catalog_url(migrated_database_url: str) -> Iterator[str]:
    """The migrated test database through the engine's synchronous driver, catalog tables emptied."""
    url = sync_database_url(migrated_database_url)
    engine = make_engine_from_url(url)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {CATALOG_TABLES} RESTART IDENTITY CASCADE"))
    engine.dispose()
    yield url


def _sources() -> LegacySources:
    return LegacySources(
        products=[
            {"product_id": "1", "product_name": "Kem ABA", "folder": "aba", "barcode": "8930000000017"},
            {"product_id": "2", "product_name": "Kem ABC", "folder": "abc"},
        ],
        product_ids={"1": "aba", "2": "abc"},
        product_ids_next=3,
        colors={"BE203": {"name": "BE203", "rgb": [109, 63, 62], "hex": "#6D3F3E"}},
    )


def test_engine_reads_the_same_catalog_from_postgres_as_from_sqlite(catalog_url: str, tmp_path: Path) -> None:
    plan = build_plan(_sources())
    postgres = make_engine_from_url(catalog_url)
    result = apply_plan(postgres, plan)
    postgres.dispose()
    assert result.inserted["product"] == 2

    sqlite_file = tmp_path / "catalog.db"
    sqlite = make_engine(sqlite_file)
    apply_plan(sqlite, plan)
    sqlite.dispose()

    from_postgres, from_sqlite = DatabaseCatalogRepository(catalog_url), SqliteCatalogRepository(sqlite_file)
    # the version is a hash of the catalog's content: same content, whichever database holds it
    assert from_postgres.version() == from_sqlite.version()
    assert from_postgres.folder_to_product_id() == {"aba": "1", "abc": "2"}
    assert from_postgres.color_references() == {"BE203": (109, 63, 62)}

    # idempotent on Postgres too: a second run inserts nothing
    postgres = make_engine_from_url(catalog_url)
    again = apply_plan(postgres, plan)
    postgres.dispose()
    assert again.inserted["product"] == 0 and again.skipped["product"] == 2


def test_barcode_is_unique_only_when_present(catalog_url: str) -> None:
    engine = make_engine_from_url(catalog_url)
    with Session(engine) as session:
        session.add(Product(product_id="1", product_name="A", barcode="8930000000017"))
        session.add(Product(product_id="2", product_name="B"))  # no barcode
        session.add(Product(product_id="3", product_name="C", barcode=""))  # empty: not a barcode
        session.add(Product(product_id="4", product_name="D"))
        session.commit()
        session.add(Product(product_id="5", product_name="E", barcode="8930000000017"))
        with pytest.raises(IntegrityError, match="ux_product_barcode"):
            session.commit()
    engine.dispose()


def test_gallery_sync_allocates_ids_on_postgres(catalog_url: str, tmp_path: Path) -> None:
    engine = make_engine_from_url(catalog_url)
    apply_plan(engine, build_plan(_sources()))
    gallery = tmp_path / "gallery"
    for folder in ("aba", "abc", "new_product"):
        (gallery / folder).mkdir(parents=True)
        (gallery / folder / "1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    result = sync_gallery(engine, gallery)
    engine.dispose()
    assert result.created == {"3": "new_product"}  # next_product_id was 3; ids are never reused
    catalog = DatabaseCatalogRepository(catalog_url)
    assert catalog.folder_to_product_id()["new_product"] == "3"
    assert catalog.get_product("3")["needs_naming"] is True
