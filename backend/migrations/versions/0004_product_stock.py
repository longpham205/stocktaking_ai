"""product_stock: the quantity on hand of each tracked product

Paying an order takes its quantities off, voiding a paid order puts them back. A product without
a row is not tracked.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_stock",
        sa.Column("product_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("product_id", name=op.f("pk_product_stock")),
    )


def downgrade() -> None:
    op.drop_table("product_stock")
