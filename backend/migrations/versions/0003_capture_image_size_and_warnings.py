"""captures: image_width, image_height, warnings

The order view draws each capture's boxes on its photo (it needs the photo's size), and the job
result tells the cashier about overlap and unrecognised objects (kept with the capture).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("captures", sa.Column("image_width", sa.Integer(), nullable=True))
    op.add_column("captures", sa.Column("image_height", sa.Integer(), nullable=True))
    op.add_column("captures", sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("captures", "warnings")
    op.drop_column("captures", "image_height")
    op.drop_column("captures", "image_width")
