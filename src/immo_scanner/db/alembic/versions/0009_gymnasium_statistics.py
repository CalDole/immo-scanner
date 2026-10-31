"""Gymnasium statistics from gymnasium-berlin.net

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gymnasium_statistics",
        sa.Column("slug", sa.String(128), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("abi_average", sa.Float),
        sa.Column("abi_year", sa.String(16)),
        sa.Column("abi_pass_rate_pct", sa.Float),
        sa.Column("abi_pass_rate_year", sa.String(16)),
        sa.Column("admission_places", sa.Integer),
        sa.Column("first_choices", sa.Integer),
        sa.Column("first_choice_pct", sa.Float),
        sa.Column("admissions_year", sa.String(16)),
        sa.Column("students", sa.Integer),
        sa.Column("students_year", sa.String(16)),
        sa.Column("teachers", sa.Integer),
        sa.Column("teachers_year", sa.String(16)),
        sa.Column("migration_background_pct", sa.Float),
        sa.Column("migration_year", sa.String(16)),
        sa.Column("type_level", sa.Integer),
    )


def downgrade() -> None:
    op.drop_table("gymnasium_statistics")
