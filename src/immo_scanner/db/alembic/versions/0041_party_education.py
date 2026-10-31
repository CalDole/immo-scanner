"""Voter education shares by party

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from immo_scanner.reference.party_education import COLUMNS, ROWS

    table = op.create_table(
        "party_education",
        sa.Column("party_key", sa.String(16), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("hauptschule_pct", sa.Float, nullable=False),
        sa.Column("mittlere_reife_pct", sa.Float, nullable=False),
        sa.Column("abitur_pct", sa.Float, nullable=False),
        sa.Column("hochschule_pct", sa.Float, nullable=False),
    )
    op.bulk_insert(table, [dict(zip(COLUMNS, row, strict=True)) for row in ROWS])


def downgrade() -> None:
    op.drop_table("party_education")
