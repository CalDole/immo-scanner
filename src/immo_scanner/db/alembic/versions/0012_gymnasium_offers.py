"""Gymnasium intake year, Schnelllerner, Europa-Schule and profile flags

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLAG_COLUMNS = (
    "starts_year_5",
    "starts_year_7",
    "schnelllerner",
    "europa_schule",
    "profile_language",
    "profile_music_art",
    "profile_mint",
    "profile_sport",
)


def upgrade() -> None:
    for name in FLAG_COLUMNS:
        op.add_column(
            "gymnasium_statistics",
            sa.Column(name, sa.Boolean, nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    for name in FLAG_COLUMNS:
        op.drop_column("gymnasium_statistics", name)
