"""initial web schema: users, shifts, orders, order_items, captures, product_prices, settings,
config_overrides, change_log (the legacy web's tables, on Postgres types)

Revision ID: 0001
Revises:
Create Date: 2026-10-04 09:01:54.996942
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "config_overrides",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_config_overrides")),
    )
    op.create_table(
        "product_prices",
        sa.Column("product_id", sa.String(length=64), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price >= 0", name=op.f("ck_product_prices_price_not_negative")),
        sa.PrimaryKeyConstraint("product_id", name=op.f("pk_product_prices")),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.String(length=80), server_default="", nullable=False),
        sa.Column("role", sa.String(length=16), server_default="staff", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("has_seen_onboarding", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('staff', 'admin')", name=op.f("ck_users_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "change_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.String(length=128), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.BigInteger(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["changed_by"], ["users.id"], name=op.f("fk_change_log_changed_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_log")),
    )
    op.create_index("ix_change_log_table_name_record_id", "change_log", ["table_name", "record_id"], unique=False)
    op.create_table(
        "shifts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_collected", sa.BigInteger(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_shifts_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shifts")),
    )
    op.create_index(
        "ux_shifts_user_id_open", "shifts", ["user_id"], unique=True, postgresql_where=sa.text("ended_at IS NULL")
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("shift_id", sa.BigInteger(), nullable=True),
        sa.Column("cashier_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("payment_method", sa.String(length=16), nullable=True),
        sa.Column("cash_given", sa.BigInteger(), nullable=True),
        sa.Column("change_given", sa.BigInteger(), nullable=True),
        sa.Column("total_amount", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'paid', 'void')", name=op.f("ck_orders_status")),
        sa.ForeignKeyConstraint(["cashier_id"], ["users.id"], name=op.f("fk_orders_cashier_id_users")),
        sa.ForeignKeyConstraint(["shift_id"], ["shifts.id"], name=op.f("fk_orders_shift_id_shifts")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index("ix_orders_cashier_id_created_at", "orders", ["cashier_id", "created_at"], unique=False)
    op.create_table(
        "captures",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("job_status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("job_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("item_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processing_time_ms", sa.Float(), nullable=True),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("detections", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "job_status IN ('queued', 'processing', 'done', 'error')", name=op.f("ck_captures_job_status")
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_captures_order_id_orders")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_captures")),
    )
    op.create_index(op.f("ix_captures_order_id"), "captures", ["order_id"], unique=False)
    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=True),
        sa.Column("manual_price", sa.BigInteger(), nullable=True),
        sa.Column("flagged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("thumb_path", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity >= 1", name=op.f("ck_order_items_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_items_order_id_orders"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")
    op.drop_index(op.f("ix_captures_order_id"), table_name="captures")
    op.drop_table("captures")
    op.drop_index("ix_orders_cashier_id_created_at", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ux_shifts_user_id_open", table_name="shifts", postgresql_where=sa.text("ended_at IS NULL"))
    op.drop_table("shifts")
    op.drop_index("ix_change_log_table_name_record_id", table_name="change_log")
    op.drop_table("change_log")
    op.drop_table("users")
    op.drop_table("settings")
    op.drop_table("product_prices")
    op.drop_table("config_overrides")
