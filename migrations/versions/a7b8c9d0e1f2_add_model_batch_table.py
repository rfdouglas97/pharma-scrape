"""add model batch table

Revision ID: a7b8c9d0e1f2
Revises: f1c2d3e4a5b6
Create Date: 2026-06-17 16:10:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f1c2d3e4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_batch",
        sa.Column("model_batch_id", sa.String(length=26), nullable=False),
        sa.Column("provider_batch_id", sa.String(length=96), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("model_batch_id"),
        sa.UniqueConstraint("provider_batch_id"),
    )
    op.create_index("ix_model_batch_kind_status", "model_batch", ["kind", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_model_batch_kind_status", table_name="model_batch")
    op.drop_table("model_batch")
