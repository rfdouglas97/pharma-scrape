"""add autonomous qa gate

Revision ID: f1c2d3e4a5b6
Revises: 02d7a88188d4
Create Date: 2026-06-17 15:05:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f1c2d3e4a5b6"
down_revision: str | None = "02d7a88188d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("company", sa.Column(
        "pipeline_status", sa.String(length=32), nullable=False,
        server_default="unverified_source",
    ))
    op.add_column("company_source", sa.Column(
        "preferred_source_rank", sa.Integer(), nullable=False, server_default="50",
    ))
    op.add_column("company_source", sa.Column("known_expected_count", sa.Integer(), nullable=True))
    op.add_column("extraction", sa.Column("qa_status", sa.String(length=32), nullable=True))
    op.add_column("extraction", sa.Column("qa_confidence", sa.Numeric(precision=4, scale=3), nullable=True))
    op.add_column("extraction", sa.Column(
        "qa_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    ))
    op.add_column("extraction", sa.Column("expected_count", sa.Integer(), nullable=True))
    op.add_column("extraction", sa.Column("observed_count", sa.Integer(), nullable=True))
    op.add_column("extraction", sa.Column(
        "repair_attempts", sa.Integer(), nullable=False, server_default="0",
    ))
    op.create_table(
        "qa_report",
        sa.Column("qa_report_id", sa.String(length=26), nullable=False),
        sa.Column("extraction_id", sa.String(length=26), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("expected_count", sa.Integer(), nullable=True),
        sa.Column("observed_count", sa.Integer(), nullable=True),
        sa.Column("missing_assets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("extra_assets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suspicious_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("count_mismatches", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["extraction_id"], ["extraction.extraction_id"]),
        sa.PrimaryKeyConstraint("qa_report_id"),
    )
    op.create_index(
        "ix_qa_report_extraction_created",
        "qa_report",
        ["extraction_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_qa_report_extraction_created", table_name="qa_report")
    op.drop_table("qa_report")
    op.drop_column("extraction", "repair_attempts")
    op.drop_column("extraction", "observed_count")
    op.drop_column("extraction", "expected_count")
    op.drop_column("extraction", "qa_report")
    op.drop_column("extraction", "qa_confidence")
    op.drop_column("extraction", "qa_status")
    op.drop_column("company_source", "known_expected_count")
    op.drop_column("company_source", "preferred_source_rank")
    op.drop_column("company", "pipeline_status")
