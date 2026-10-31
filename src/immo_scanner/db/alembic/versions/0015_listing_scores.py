"""Numeric heating demand and listing scores on listing_features

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENERGY_HOOK = (
    "    COALESCE(nbk.available_from, is24.available_from) AS available_from,\n"
)
ENERGY_COLUMN = (
    ENERGY_HOOK
    + "    COALESCE(is24.energy_efficiency_class, nbk.energy_efficiency_class)"
    + " AS energy_efficiency_class,\n"
)


def _inner_listing_features_sql() -> str:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0011_expose_fields.py")
    spec = importlib.util.spec_from_file_location("expose", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    sql = previous._listing_features_sql()
    if ENERGY_HOOK not in sql:
        raise RuntimeError("listing_features SQL no longer has the available_from hook")
    return sql.replace(ENERGY_HOOK, ENERGY_COLUMN, 1)


def _scored_listing_features_sql() -> str:
    from immo_scanner.explore.scoring import (
        add_group_score_sql,
        add_total_score_sql,
        wrap_listing_features_sql,
    )

    inner = wrap_listing_features_sql(_inner_listing_features_sql())
    return add_total_score_sql(add_group_score_sql(inner))


def _create_view(sql: str) -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS listing_features")
    op.execute(sql)
    op.execute("CREATE UNIQUE INDEX ix_listing_features_id ON listing_features (id)")
    op.execute("CREATE INDEX ix_listing_features_geom ON listing_features USING gist (geom)")
    op.execute("REFRESH MATERIALIZED VIEW listing_features")


def upgrade() -> None:
    from immo_scanner.reference.building_age import HEATING_DEMAND_KWH

    op.add_column("building_age_classes", sa.Column("typical_heating_demand_kwh", sa.Float))
    for code, kwh in HEATING_DEMAND_KWH.items():
        op.execute(
            sa.text(
                "UPDATE building_age_classes"
                " SET typical_heating_demand_kwh = :kwh WHERE class_code = :code"
            ).bindparams(kwh=kwh, code=code)
        )
    _create_view(_scored_listing_features_sql())


def downgrade() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0011_expose_fields.py")
    spec = importlib.util.spec_from_file_location("expose", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    _create_view(previous._listing_features_sql())
    op.drop_column("building_age_classes", "typical_heating_demand_kwh")
