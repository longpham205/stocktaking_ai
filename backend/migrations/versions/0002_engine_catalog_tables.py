"""engine catalog tables: product, product_evidence, color_reference, catalog_meta

The engine's SQLModel tables (backend/engine/catalog/db.py), created here so the web database has
one schema owner. Column types are the engine's: text timestamps (ISO-8601 UTC), JSON as text.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 09:15:53.773622
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_meta",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "color_reference",
        sa.Column("color_code", sa.String(), nullable=False),
        sa.Column("r", sa.Integer(), nullable=False),
        sa.Column("g", sa.Integer(), nullable=False),
        sa.Column("b", sa.Integer(), nullable=False),
        sa.Column("hex", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint("source IN ('seed', 'manual', 'gallery')", name="ck_color_source"),
        sa.CheckConstraint("b BETWEEN 0 AND 255", name="ck_color_b"),
        sa.CheckConstraint("g BETWEEN 0 AND 255", name="ck_color_g"),
        sa.CheckConstraint("r BETWEEN 0 AND 255", name="ck_color_r"),
        sa.PrimaryKeyConstraint("color_code"),
    )
    op.create_table(
        "product",
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("product_name", sa.String(), nullable=False),
        sa.Column("brand", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("barcode", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("gallery_folder", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("needs_naming", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint("image_count >= 0", name="ck_product_image_count"),
        sa.PrimaryKeyConstraint("product_id"),
    )
    op.create_index(
        "ux_product_barcode",
        "product",
        ["barcode"],
        unique=True,
        postgresql_where=sa.text("barcode IS NOT NULL AND barcode <> ''"),
    )
    op.create_index(
        "ux_product_gallery_folder",
        "product",
        ["gallery_folder"],
        unique=True,
        postgresql_where=sa.text("gallery_folder IS NOT NULL AND gallery_folder <> ''"),
    )
    op.create_table(
        "product_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("evidence_type", sa.String(), nullable=False),
        sa.Column("value_json", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["product.product_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "evidence_type", name="ux_evidence_product_type"),
    )
    op.create_index(op.f("ix_product_evidence_product_id"), "product_evidence", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_product_evidence_product_id"), table_name="product_evidence")
    op.drop_table("product_evidence")
    op.drop_index(
        "ux_product_gallery_folder",
        table_name="product",
        postgresql_where=sa.text("gallery_folder IS NOT NULL AND gallery_folder <> ''"),
    )
    op.drop_index(
        "ux_product_barcode",
        table_name="product",
        postgresql_where=sa.text("barcode IS NOT NULL AND barcode <> ''"),
    )
    op.drop_table("product")
    op.drop_table("color_reference")
    op.drop_table("catalog_meta")
