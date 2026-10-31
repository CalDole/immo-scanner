"""Sekundarschule statistics from sekundarschulen-berlin.de

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sekundarschule_statistics",
        sa.Column("slug", sa.String(128), primary_key=True),
        sa.Column("bsn", sa.String(8)),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("abi_average", sa.Float),
        sa.Column("abi_year", sa.String(16)),
        sa.Column("abi_pass_rate_pct", sa.Float),
        sa.Column("abi_pass_rate_year", sa.String(16)),
        sa.Column("abi_pass_rate_prev_pct", sa.Float),
        sa.Column("admission_places", sa.Integer),
        sa.Column("first_choices", sa.Integer),
        sa.Column("first_choice_pct", sa.Float),
        sa.Column("admissions_year", sa.String(16)),
        sa.Column("migration_background_pct", sa.Float),
        sa.Column("migration_background_prev_pct", sa.Float),
        sa.Column("migration_year", sa.String(16)),
        sa.Column("type_level", sa.Integer),
    )
    op.create_index("ix_sekundarschule_statistics_bsn", "sekundarschule_statistics", ["bsn"])


def downgrade() -> None:
    op.drop_index("ix_sekundarschule_statistics_bsn", table_name="sekundarschule_statistics")
    op.drop_table("sekundarschule_statistics")
