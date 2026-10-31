"""ThinkImmo data in listing_features

`thinkimmo_properties.listing_id` links a ThinkImmo property to the listing of an
own crawler with the same portal ad, or to the property's own `thinkimmo` listing.
listing_features gets ThinkImmo's valuation, cash flow and foreclosure flag, and
uses its facts where the portal exposés left gaps.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

YEAR = "COALESCE(d.year_built, iw.year_built, s.year_built)"
REPLACEMENTS = {
    YEAR: "COALESCE(d.year_built, iw.year_built, s.year_built, ti.year_built)",
    "    COALESCE(d.floor, nbk.floor, iw.floor, s.floor) AS floor,\n": (
        "    COALESCE(d.floor, nbk.floor, iw.floor, s.floor, ti.floor) AS floor,\n"
    ),
    "    COALESCE(d.total_floors, iw.total_floors) AS total_floors,\n": (
        "    COALESCE(d.total_floors, iw.total_floors, ti.total_floors) AS total_floors,\n"
    ),
    "    COALESCE(d.last_renovation_year, iw.last_renovation_year) AS last_renovation_year,\n": (
        "    COALESCE(d.last_renovation_year, iw.last_renovation_year, ti.last_renovation_year)"
        " AS last_renovation_year,\n"
    ),
    "extract(year FROM now())::int - COALESCE(d.last_renovation_year, iw.last_renovation_year)": (
        "extract(year FROM now())::int"
        " - COALESCE(d.last_renovation_year, iw.last_renovation_year, ti.last_renovation_year)"
    ),
    "    COALESCE(d.has_elevator, ka.has_elevator) AS has_elevator,\n": (
        "    COALESCE(d.has_elevator, ka.has_elevator, ti.has_elevator) AS has_elevator,\n"
    ),
    "    COALESCE(d.has_cellar, is24.has_cellar) AS has_cellar,\n": (
        "    COALESCE(d.has_cellar, is24.has_cellar, ti.has_cellar) AS has_cellar,\n"
    ),
    "             is24.has_balcony_terrace) AS has_balcony,\n": (
        "             is24.has_balcony_terrace, ti.has_balcony) AS has_balcony,\n"
    ),
    "    COALESCE(iw.has_fitted_kitchen, ka.has_fitted_kitchen,": (
        "    COALESCE(iw.has_fitted_kitchen, ka.has_fitted_kitchen, ti.has_fitted_kitchen,"
    ),
    "        is24.is_rented,\n": "        is24.is_rented,\n        ti.is_rented,\n",
    "    COALESCE(iw.energy_efficiency_class, is24.energy_efficiency_class,"
    " nbk.energy_efficiency_class) AS energy_efficiency_class,\n": (
        "    COALESCE(iw.energy_efficiency_class, is24.energy_efficiency_class,"
        " nbk.energy_efficiency_class, NULLIF(ti.energy_efficiency_class, 'NO_INFORMATION'))"
        " AS energy_efficiency_class,\n"
    ),
    "    iw.has_monument_protection,\n": (
        "    iw.has_monument_protection,\n"
        "    ti.id AS thinkimmo_id,\n"
        "    ti.platform_names AS thinkimmo_platforms,\n"
        "    ti.is_foreclosure,\n"
        "    ti.is_leasehold,\n"
        "    ti.market_price_per_m2 AS thinkimmo_market_price_per_m2,\n"
        "    ti.price_to_market_pct AS thinkimmo_price_to_market_pct,\n"
        "    ti.estimated_rent AS thinkimmo_estimated_rent,\n"
        "    ti.gross_return AS thinkimmo_gross_return,\n"
        "    ti.cash_flow AS thinkimmo_cash_flow,\n"
        "    ti.location_score AS thinkimmo_location_score,\n"
        "    ti.views AS thinkimmo_views,\n"
    ),
    "    COALESCE(s.hausgeld, iw.hausgeld) AS hausgeld,\n": (
        "    COALESCE(s.hausgeld, iw.hausgeld, ti.hausgeld) AS hausgeld,\n"
    ),
    "LEFT JOIN immowelt_exposes iw ON iw.listing_id = l.id\n": (
        "LEFT JOIN immowelt_exposes iw ON iw.listing_id = l.id\n"
        # listing_features has a unique index on id, so one property per listing.
        "LEFT JOIN LATERAL (\n"
        "    SELECT * FROM thinkimmo_properties t WHERE t.listing_id = l.id\n"
        "    ORDER BY t.last_seen DESC LIMIT 1\n"
        ") ti ON true\n"
    ),
}


def _immowelt_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0019_immowelt_exposes.py")
    spec = importlib.util.spec_from_file_location("immowelt", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _immowelt_migration()._listing_features_sql()
    for old, new in REPLACEMENTS.items():
        found = sql.count(old)
        # Building age repeats the year expression in the column and in the subtraction.
        expected = 2 if old == YEAR else 1
        if found != expected:
            raise RuntimeError(f"expected {expected} copies of {old!r}, found {found}")
        sql = sql.replace(old, new)
    return sql


def _create_view(sql: str) -> None:
    gaps = _immowelt_migration()._gaps_migration()
    gaps._rented_migration()._scores_migration()._create_view(sql)


def upgrade() -> None:
    op.add_column(
        "thinkimmo_properties",
        sa.Column("listing_id", sa.Integer, sa.ForeignKey("listings.id", ondelete="SET NULL")),
    )
    op.create_index(
        "ix_thinkimmo_properties_listing_id", "thinkimmo_properties", ["listing_id"]
    )
    _create_view(_listing_features_sql())


def downgrade() -> None:
    _create_view(_immowelt_migration()._listing_features_sql())
    op.drop_index("ix_thinkimmo_properties_listing_id", table_name="thinkimmo_properties")
    op.drop_column("thinkimmo_properties", "listing_id")
