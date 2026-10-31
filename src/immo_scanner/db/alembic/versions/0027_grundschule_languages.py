"""Allow longer Grundschule language labels

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "grundschulen",
        "partner_language",
        existing_type=sa.String(64),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "grundschulen",
        "first_foreign_language",
        existing_type=sa.String(64),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "grundschulen",
        "partner_language",
        existing_type=sa.Text(),
        type_=sa.String(64),
        existing_nullable=True,
    )
    op.alter_column(
        "grundschulen",
        "first_foreign_language",
        existing_type=sa.Text(),
        type_=sa.String(64),
        existing_nullable=True,
    )
