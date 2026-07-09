"""Initial_tables

Revision ID: 8a80d47ffdf3
Revises:
Create Date: 2026-07-09 22:49:27.089202

Creates the five core tables for AutoEntry Bot's inventory and tenant
management system.  All table definitions are authored manually because
the project uses SQLAlchemy Core raw ``text()`` SQL — no ORM models
exist, so ``--autogenerate`` produces empty migrations.

Table dependency order (FK constraints demand this sequence):
    1. tenants           (no FK)
    2. telegram_users    (FK → tenants)
    3. inventory_snapshots (no FK)
    4. inventory_items   (FK → inventory_snapshots)
    5. discrepancy_items (no PK-only FK; references snapshots conceptually)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# ---------------------------------------------------------------------------
# Revision identifiers, used by Alembic.
# ---------------------------------------------------------------------------
revision: str = "8a80d47ffdf3"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the five core tables and performance indexes."""
    # ------------------------------------------------------------------
    # 1. tenants
    # ------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenants"),
    )

    # ------------------------------------------------------------------
    # 2. telegram_users (FK → tenants.tenant_id)
    # ------------------------------------------------------------------
    op.create_table(
        "telegram_users",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'user'")),
        sa.PrimaryKeyConstraint("telegram_user_id", name="pk_telegram_users"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
            name="fk_telegram_users_tenant_id",
            ondelete="CASCADE",
        ),
    )

    # Performance index for tenant-scoped lookups of Telegram users
    op.create_index(
        "ix_telegram_users_tenant_id",
        "telegram_users",
        ["tenant_id"],
    )

    # ------------------------------------------------------------------
    # 3. inventory_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "inventory_snapshots",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("store_id", sa.Text(), nullable=False),
        sa.Column(
            "parsed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_snapshots"),
    )

    # Composite index for tenant-scoped snapshot queries
    op.create_index(
        "ix_inventory_snapshots_tenant_id_parsed_at",
        "inventory_snapshots",
        ["tenant_id", "parsed_at"],
    )

    # ------------------------------------------------------------------
    # 4. inventory_items (FK → inventory_snapshots.id)
    # ------------------------------------------------------------------
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("system_qty", sa.Integer(), nullable=False),
        sa.Column("actual_qty", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_items"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["inventory_snapshots.id"],
            name="fk_inventory_items_snapshot_id",
            ondelete="CASCADE",
        ),
    )

    # Composite index for the common query pattern: tenant + SKU
    op.create_index(
        "ix_inventory_items_tenant_id_sku",
        "inventory_items",
        ["tenant_id", "sku"],
    )

    # Index on snapshot_id for snapshot-scoped lookups
    op.create_index(
        "ix_inventory_items_snapshot_id",
        "inventory_items",
        ["snapshot_id"],
    )

    # ------------------------------------------------------------------
    # 5. discrepancy_items
    # ------------------------------------------------------------------
    # discrepancy_items does not have a dedicated id column in the raw
    # SQL bulk insert; we add a surrogate UUID-as-text PK to give Alembic
    # a proper primary key and enable future row-level operations.
    op.create_table(
        "discrepancy_items",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("system_qty", sa.Integer(), nullable=False),
        sa.Column("actual_qty", sa.Integer(), nullable=False),
        sa.Column("diff_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_discrepancy_items"),
    )

    # Composite index for discrepancy queries by tenant + status
    op.create_index(
        "ix_discrepancy_items_tenant_id_status",
        "discrepancy_items",
        ["tenant_id", "status"],
    )

    # Index on snapshot_id for snapshot-scoped lookups
    op.create_index(
        "ix_discrepancy_items_snapshot_id",
        "discrepancy_items",
        ["snapshot_id"],
    )


def downgrade() -> None:
    """Drop tables in reverse dependency order."""
    op.drop_table("discrepancy_items")
    op.drop_table("inventory_items")
    op.drop_table("inventory_snapshots")
    op.drop_table("telegram_users")
    op.drop_table("tenants")