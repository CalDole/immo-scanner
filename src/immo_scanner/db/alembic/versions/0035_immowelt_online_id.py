"""Widen Immowelt online_id so exposé UUIDs fit

Immowelt classified ids are 36-character UUIDs. character varying(32) rejected those
rows during the exposé fetch.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "immowelt_exposes",
        "online_id",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "immowelt_exposes",
        "online_id",
        existing_type=sa.String(64),
        type_=sa.String(32),
        existing_nullable=True,
    )
