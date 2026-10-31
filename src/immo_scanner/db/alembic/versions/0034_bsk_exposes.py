"""BSK Immobilien exposés on listing_features

bsk_exposes stores the Objektdetails table of a BSK exposé. Year, floor, heating and
the balcony, cellar, elevator and parking flags reach listing_features through
listing_details; the remaining columns are added to the COALESCEs here.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPLACEMENTS = {
    "    COALESCE(iw.bedrooms, ka.bedrooms,": (
        "    COALESCE(iw.bedrooms, bsk.bedrooms, ka.bedrooms,"
    ),
    "    COALESCE(iw.bathrooms, ka.bathrooms,": (
        "    COALESCE(iw.bathrooms, bsk.bathrooms, ka.bathrooms,"
    ),
    "    COALESCE(ka.apartment_type, nbk.unit_type, is24.property_type) AS apartment_type,\n": (
        "    COALESCE(ka.apartment_type, nbk.unit_type, is24.property_type, bsk.property_type)"
        " AS apartment_type,\n"
    ),
    "    COALESCE(iw.buyer_commission, ka.provision,": (
        "    COALESCE(iw.buyer_commission, bsk.buyer_commission, ka.provision,"
    ),
    "    COALESCE(iw.has_terrace, ka.has_terrace,": (
        "    COALESCE(iw.has_terrace, bsk.has_terrace, ka.has_terrace,"
    ),
    "    COALESCE(iw.has_fitted_kitchen, ka.has_fitted_kitchen,": (
        "    COALESCE(iw.has_fitted_kitchen, bsk.has_fitted_kitchen, ka.has_fitted_kitchen,"
    ),
    "    COALESCE(iw.available_from, nbk.available_from,": (
        "    COALESCE(iw.available_from, bsk.available_from, nbk.available_from,"
    ),
    "        iw.is_rented,\n        is24.is_rented,\n": (
        "        iw.is_rented,\n        is24.is_rented,\n        bsk.is_rented,\n"
    ),
    "    COALESCE(iw.energy_efficiency_class, is24.energy_efficiency_class,": (
        "    COALESCE(iw.energy_efficiency_class, is24.energy_efficiency_class,"
        " bsk.energy_efficiency_class,"
    ),
    "    COALESCE(iw.final_energy_kwh_m2a, is24.final_energy_kwh_m2a,": (
        "    COALESCE(iw.final_energy_kwh_m2a, is24.final_energy_kwh_m2a, bsk.final_energy_kwh_m2a,"
    ),
    "    COALESCE(iw.property_condition, is24.property_condition) AS property_condition,\n"
    "    is24.interior_quality,\n"
    "    is24.has_guest_toilet,\n"
    "    is24.parking_type,\n": (
        "    COALESCE(iw.property_condition, is24.property_condition, bsk.property_condition)"
        " AS property_condition,\n"
        "    COALESCE(is24.interior_quality, bsk.interior_quality) AS interior_quality,\n"
        "    COALESCE(is24.has_guest_toilet, bsk.has_guest_toilet) AS has_guest_toilet,\n"
        "    COALESCE(is24.parking_type, bsk.parking_type) AS parking_type,\n"
    ),
    "    iw.flooring,\n    iw.bathroom_features,\n    iw.has_monument_protection,\n": (
        "    COALESCE(iw.flooring, bsk.flooring) AS flooring,\n"
        "    COALESCE(iw.bathroom_features, bsk.bathroom_features) AS bathroom_features,\n"
        "    COALESCE(iw.has_monument_protection, bsk.has_monument_protection)"
        " AS has_monument_protection,\n"
    ),
    "    COALESCE(s.hausgeld, iw.hausgeld, ti.hausgeld) AS hausgeld,\n": (
        "    COALESCE(s.hausgeld, iw.hausgeld, bsk.hausgeld, ti.hausgeld) AS hausgeld,\n"
    ),
    "LEFT JOIN immowelt_exposes iw ON iw.listing_id = l.id\n": (
        "LEFT JOIN immowelt_exposes iw ON iw.listing_id = l.id\n"
        "LEFT JOIN bsk_exposes bsk ON bsk.listing_id = l.id\n"
    ),
}


def _is24_amenities():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0033_is24_amenities.py")
    spec = importlib.util.spec_from_file_location("is24_amenities", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _is24_amenities()._listing_features_sql()
    for old, new in REPLACEMENTS.items():
        if sql.count(old) != 1:
            raise RuntimeError(f"listing_features SQL no longer has one {old.strip()!r}")
        sql = sql.replace(old, new, 1)
    return sql


def _create_view(sql: str) -> None:
    _is24_amenities()._create_view(sql)


def _create_table() -> None:
    op.create_table(
        "bsk_exposes",
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("object_id", sa.String(32)),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("bezirk", sa.Text),
        sa.Column("ortsteil", sa.Text),
        sa.Column("property_type", sa.Text),
        sa.Column("price", sa.Float),
        sa.Column("living_area", sa.Float),
        sa.Column("usable_area", sa.Float),
        sa.Column("rooms", sa.Float),
        sa.Column("bedrooms", sa.Float),
        sa.Column("bathrooms", sa.Float),
        sa.Column("floor", sa.Integer),
        sa.Column("total_floors", sa.Integer),
        sa.Column("year_built", sa.Integer),
        sa.Column("property_condition", sa.Text),
        sa.Column("interior_quality", sa.Text),
        sa.Column("heating_type", sa.Text),
        sa.Column("energy_source", sa.Text),
        sa.Column("energy_certificate_type", sa.Text),
        sa.Column("final_energy_type", sa.String(32)),
        sa.Column("final_energy_kwh_m2a", sa.Float),
        sa.Column("energy_efficiency_class", sa.String(8)),
        sa.Column("energy_certificate_valid_until", sa.Text),
        sa.Column("is_rented", sa.Boolean),
        sa.Column("monthly_rent", sa.Float),
        sa.Column("available_from", sa.Text),
        sa.Column("hausgeld", sa.Float),
        sa.Column("buyer_commission", sa.Text),
        sa.Column("flooring", sa.Text),
        sa.Column("bathroom_features", sa.Text),
        sa.Column("balcony_count", sa.Integer),
        sa.Column("terrace_count", sa.Integer),
        sa.Column("parking_spaces", sa.Integer),
        sa.Column("parking_type", sa.Text),
        sa.Column("has_balcony", sa.Boolean),
        sa.Column("has_terrace", sa.Boolean),
        sa.Column("has_cellar", sa.Boolean),
        sa.Column("has_elevator", sa.Boolean),
        sa.Column("has_parking", sa.Boolean),
        sa.Column("has_fitted_kitchen", sa.Boolean),
        sa.Column("has_garden", sa.Boolean),
        sa.Column("has_guest_toilet", sa.Boolean),
        sa.Column("has_monument_protection", sa.Boolean),
        sa.Column("is_barrier_free", sa.Boolean),
        sa.Column("equipment_text", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("location_text", sa.Text),
        sa.Column("photo_count", sa.Integer),
        sa.Column("floor_plan_count", sa.Integer),
        sa.Column("media_dir", sa.Text),
        sa.Column("media", JSONB, nullable=False, server_default="[]"),
        sa.Column("facts", JSONB, nullable=False, server_default="{}"),
        sa.Column("features", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index("ix_bsk_exposes_object_id", "bsk_exposes", ["object_id"])


def upgrade() -> None:
    _create_table()
    _create_view(_listing_features_sql())


def downgrade() -> None:
    _create_view(_is24_amenities()._listing_features_sql())
    op.drop_index("ix_bsk_exposes_object_id", table_name="bsk_exposes")
    op.drop_table("bsk_exposes")
