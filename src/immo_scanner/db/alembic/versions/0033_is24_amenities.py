"""ImmoScout24 parking, kitchen, guest toilet, condition and energy type

The exposé columns feed the parking and kitchen scores and the condition column of
listing_features; interior quality, guest toilet and parking details are added to it.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    sa.Column("final_energy_type", sa.String(32)),
    sa.Column("property_condition", sa.Text),
    sa.Column("interior_quality", sa.Text),
    sa.Column("has_parking", sa.Boolean),
    sa.Column("parking_type", sa.Text),
    sa.Column("parking_spaces", sa.Integer),
    sa.Column("parking_price", sa.Float),
    sa.Column("has_fitted_kitchen", sa.Boolean),
    sa.Column("has_guest_toilet", sa.Boolean),
    sa.Column("features", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
)

REPLACEMENTS = {
    "    d.has_parking,\n": "    COALESCE(d.has_parking, is24.has_parking) AS has_parking,\n",
    "    COALESCE(iw.has_fitted_kitchen, ka.has_fitted_kitchen,": (
        "    COALESCE(iw.has_fitted_kitchen, ka.has_fitted_kitchen, is24.has_fitted_kitchen,"
    ),
    "    iw.property_condition,\n": (
        "    COALESCE(iw.property_condition, is24.property_condition) AS property_condition,\n"
        "    is24.interior_quality,\n"
        "    is24.has_guest_toilet,\n"
        "    is24.parking_type,\n"
        "    is24.parking_price,\n"
    ),
}


def _crime_scores():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0032_crime_incident_scores.py")
    spec = importlib.util.spec_from_file_location("crime_incident_scores", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _crime_scores()._listing_features_sql()
    for old, new in REPLACEMENTS.items():
        if sql.count(old) != 1:
            raise RuntimeError(f"listing_features SQL no longer has one {old.strip()!r}")
        sql = sql.replace(old, new, 1)
    return sql


def _create_view(sql: str) -> None:
    _crime_scores()._origin()._grundschulen()._create_view(sql)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("immoscout24_exposes", column)
    _create_view(_listing_features_sql())


def downgrade() -> None:
    _create_view(_crime_scores()._listing_features_sql())
    for column in reversed(COLUMNS):
        op.drop_column("immoscout24_exposes", column.name)
