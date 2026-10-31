"""Immowelt exposé fields on listing_features

The exposé page embeds a classified object. Those columns were only filled from
the other portals, so an Immowelt flat kept an empty energy class, Hausgeld and
rental flag even after its detail page had been fetched.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPLACEMENTS = {
    "COALESCE(d.year_built, s.year_built)": "COALESCE(d.year_built, iw.year_built, s.year_built)",
    "    d.total_floors,\n": "    COALESCE(d.total_floors, iw.total_floors) AS total_floors,\n",
    "    d.last_renovation_year,\n": (
        "    COALESCE(d.last_renovation_year, iw.last_renovation_year) AS last_renovation_year,\n"
    ),
    "extract(year FROM now())::int - d.last_renovation_year": (
        "extract(year FROM now())::int - COALESCE(d.last_renovation_year, iw.last_renovation_year)"
    ),
    "    d.heating_type,\n": "    COALESCE(d.heating_type, iw.heating_type) AS heating_type,\n",
    "    COALESCE(d.floor, nbk.floor, s.floor) AS floor,\n": (
        "    COALESCE(d.floor, nbk.floor, iw.floor, s.floor) AS floor,\n"
    ),
    "    d.balcony_count,\n": (
        "    COALESCE(d.balcony_count, iw.balcony_count) AS balcony_count,\n"
    ),
    "    COALESCE(ka.bedrooms, nbk.bedrooms, is24.bedrooms) AS bedrooms,\n": (
        "    COALESCE(iw.bedrooms, ka.bedrooms, nbk.bedrooms, is24.bedrooms) AS bedrooms,\n"
    ),
    "    COALESCE(ka.bathrooms, nbk.bathrooms, is24.bathrooms) AS bathrooms,\n": (
        "    COALESCE(iw.bathrooms, ka.bathrooms, nbk.bathrooms, is24.bathrooms) AS bathrooms,\n"
    ),
    "    COALESCE(ka.provision, nbk.buyer_commission, "
    "CASE WHEN nbk.has_commission IS FALSE THEN 'Nein' END, is24.buyer_commission)"
    " AS provision,\n": (
        "    COALESCE(iw.buyer_commission, ka.provision, nbk.buyer_commission, "
        "CASE WHEN nbk.has_commission IS FALSE THEN 'Nein' END, is24.buyer_commission)"
        " AS provision,\n"
    ),
    "    COALESCE(ka.has_terrace, is24.has_terrace) AS has_terrace,\n": (
        "    COALESCE(iw.has_terrace, ka.has_terrace, is24.has_terrace) AS has_terrace,\n"
    ),
    "    COALESCE(ka.has_fitted_kitchen,"
    " CASE WHEN jsonb_exists(nbk.features, 'Einbauküche') THEN true END)"
    " AS has_fitted_kitchen,\n": (
        "    COALESCE(iw.has_fitted_kitchen, ka.has_fitted_kitchen,"
        " CASE WHEN jsonb_exists(nbk.features, 'Einbauküche') THEN true END)"
        " AS has_fitted_kitchen,\n"
    ),
    "    COALESCE(nbk.available_from, is24.available_from) AS available_from,\n": (
        "    COALESCE(iw.available_from, nbk.available_from, is24.available_from)"
        " AS available_from,\n"
    ),
    "        is24.is_rented,\n": "        iw.is_rented,\n        is24.is_rented,\n",
    "    COALESCE(is24.energy_efficiency_class, nbk.energy_efficiency_class)"
    " AS energy_efficiency_class,\n": (
        "    COALESCE(iw.energy_efficiency_class, is24.energy_efficiency_class,"
        " nbk.energy_efficiency_class) AS energy_efficiency_class,\n"
        "    COALESCE(iw.final_energy_kwh_m2a, is24.final_energy_kwh_m2a,"
        " nbk.final_energy_kwh_m2a) AS final_energy_kwh_m2a,\n"
        "    iw.property_condition,\n"
        "    iw.flooring,\n"
        "    iw.bathroom_features,\n"
        "    iw.has_monument_protection,\n"
    ),
    "    s.hausgeld,\n": "    COALESCE(s.hausgeld, iw.hausgeld) AS hausgeld,\n",
    "LEFT JOIN immoscout24_exposes is24 ON is24.listing_id = l.id\n": (
        "LEFT JOIN immoscout24_exposes is24 ON is24.listing_id = l.id\n"
        "LEFT JOIN immowelt_exposes iw ON iw.listing_id = l.id\n"
    ),
}


def _gaps_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0017_neubaukompass_gaps.py")
    spec = importlib.util.spec_from_file_location("gaps", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _gaps_migration()._listing_features_sql()
    for old, new in REPLACEMENTS.items():
        found = sql.count(old)
        # Building age repeats the year expression in the column and in the subtraction.
        if old == "COALESCE(d.year_built, s.year_built)":
            if found != 2:
                raise RuntimeError(f"expected 2 copies of the year expression, found {found}")
            sql = sql.replace(old, new)
            continue
        if found != 1:
            raise RuntimeError(f"listing_features SQL no longer has one copy of {old!r}")
        sql = sql.replace(old, new, 1)
    return sql


def _create_table() -> None:
    op.create_table(
        "immowelt_exposes",
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("online_id", sa.String(32)),
        sa.Column("reference_number", sa.Text),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("postcode", sa.String(5)),
        sa.Column("district", sa.Text),
        sa.Column("address_published", sa.Boolean),
        sa.Column("price", sa.Float),
        sa.Column("living_area", sa.Float),
        sa.Column("rooms", sa.Float),
        sa.Column("bedrooms", sa.Float),
        sa.Column("bathrooms", sa.Float),
        sa.Column("floor", sa.Integer),
        sa.Column("total_floors", sa.Integer),
        sa.Column("year_built", sa.Integer),
        sa.Column("last_renovation_year", sa.Integer),
        sa.Column("property_condition", sa.Text),
        sa.Column("heating_type", sa.Text),
        sa.Column("energy_source", sa.Text),
        sa.Column("energy_certificate_type", sa.Text),
        sa.Column("energy_efficiency_class", sa.String(8)),
        sa.Column("final_energy_kwh_m2a", sa.Float),
        sa.Column("is_rented", sa.Boolean),
        sa.Column("available_from", sa.Text),
        sa.Column("hausgeld", sa.Float),
        sa.Column("buyer_commission", sa.Text),
        sa.Column("has_balcony", sa.Boolean),
        sa.Column("balcony_count", sa.Integer),
        sa.Column("has_terrace", sa.Boolean),
        sa.Column("has_cellar", sa.Boolean),
        sa.Column("has_elevator", sa.Boolean),
        sa.Column("has_parking", sa.Boolean),
        sa.Column("has_fitted_kitchen", sa.Boolean),
        sa.Column("has_garden", sa.Boolean),
        sa.Column("has_monument_protection", sa.Boolean),
        sa.Column("flooring", sa.Text),
        sa.Column("bathroom_features", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("location_text", sa.Text),
        sa.Column("further_text", sa.Text),
        sa.Column("facts", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_immowelt_exposes_online_id", "immowelt_exposes", ["online_id"])


def upgrade() -> None:
    _create_table()
    gaps = _gaps_migration()
    gaps._rented_migration()._scores_migration()._create_view(_listing_features_sql())


def downgrade() -> None:
    gaps = _gaps_migration()
    gaps._rented_migration()._scores_migration()._create_view(gaps._listing_features_sql())
    op.drop_index("ix_immowelt_exposes_online_id", table_name="immowelt_exposes")
    op.drop_table("immowelt_exposes")
