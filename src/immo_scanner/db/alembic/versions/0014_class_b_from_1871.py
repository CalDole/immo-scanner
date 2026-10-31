"""Class B Gründerzeit period starts in 1871, not 1870

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """UPDATE building_age_classes
           SET year_from = 1871
           WHERE class_code = 'B' AND year_from = 1870 AND year_to = 1918"""
    )


def downgrade() -> None:
    op.execute(
        """UPDATE building_age_classes
           SET year_from = 1870
           WHERE class_code = 'B' AND year_from = 1871 AND year_to = 1918"""
    )
