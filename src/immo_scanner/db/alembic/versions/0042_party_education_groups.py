"""Volt, BSW and Nichtwähler education profiles

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPSERT = """
    INSERT INTO party_education (
        party_key, name, hauptschule_pct, mittlere_reife_pct, abitur_pct, hochschule_pct
    )
    VALUES (
        :party_key, :name, :hauptschule_pct, :mittlere_reife_pct, :abitur_pct, :hochschule_pct
    )
    ON CONFLICT (party_key) DO UPDATE SET
        name = EXCLUDED.name,
        hauptschule_pct = EXCLUDED.hauptschule_pct,
        mittlere_reife_pct = EXCLUDED.mittlere_reife_pct,
        abitur_pct = EXCLUDED.abitur_pct,
        hochschule_pct = EXCLUDED.hochschule_pct
"""


def _new_rows() -> list[dict[str, object]]:
    from immo_scanner.reference.party_education import COLUMNS, NEW_KEYS, ROWS

    keys = set(NEW_KEYS)
    return [dict(zip(COLUMNS, row, strict=True)) for row in ROWS if row[0] in keys]


def upgrade() -> None:
    op.get_bind().execute(sa.text(_UPSERT), _new_rows())


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM party_education WHERE party_key IN ('volt', 'bsw', 'nichtwaehler')")
    )
