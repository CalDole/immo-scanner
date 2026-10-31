"""Engel & Völkers exposés on listing_features

engelvoelkers_exposes stores the listing record of an E&V exposé page. Year, floor,
heating and the balcony, cellar, elevator and parking flags reach listing_features
through listing_details; the remaining columns are added to the COALESCEs here. 0035
left the view as 0034 built it.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPLACEMENTS = {
    "    COALESCE(iw.bedrooms, bsk.bedrooms,": (
        "    COALESCE(iw.bedrooms, bsk.bedrooms, ev.bedrooms,"
    ),
    "    COALESCE(iw.bathrooms, bsk.bathrooms,": (
        "    COALESCE(iw.bathrooms, bsk.bathrooms, ev.bathrooms,"
    ),
    " is24.property_type, bsk.property_type)": (
        " is24.property_type, bsk.property_type, ev.property_type)"
    ),
    "    COALESCE(iw.buyer_commission, bsk.buyer_commission,": (
        "    COALESCE(iw.buyer_commission, bsk.buyer_commission, ev.buyer_commission,"
    ),
    "    COALESCE(iw.has_terrace, bsk.has_terrace,": (
        "    COALESCE(iw.has_terrace, bsk.has_terrace, ev.has_terrace,"
    ),
    "    COALESCE(iw.has_fitted_kitchen, bsk.has_fitted_kitchen,": (
        "    COALESCE(iw.has_fitted_kitchen, bsk.has_fitted_kitchen, ev.has_fitted_kitchen,"
    ),
    "    COALESCE(iw.available_from, bsk.available_from,": (
        "    COALESCE(iw.available_from, bsk.available_from, ev.available_from,"
    ),
    "        bsk.is_rented,\n": "        bsk.is_rented,\n        ev.is_rented,\n",
    " bsk.energy_efficiency_class,": " bsk.energy_efficiency_class, ev.energy_efficiency_class,",
    " bsk.final_energy_kwh_m2a,": " bsk.final_energy_kwh_m2a, ev.final_energy_kwh_m2a,",
    " bsk.property_condition)": " bsk.property_condition, ev.property_condition)",
    "    COALESCE(is24.has_guest_toilet, bsk.has_guest_toilet)": (
        "    COALESCE(is24.has_guest_toilet, bsk.has_guest_toilet, ev.has_guest_toilet)"
    ),
    "    COALESCE(iw.flooring, bsk.flooring)": (
        "    COALESCE(iw.flooring, bsk.flooring, ev.flooring)"
    ),
    "    COALESCE(iw.has_monument_protection, bsk.has_monument_protection)": (
        "    COALESCE(iw.has_monument_protection, bsk.has_monument_protection,"
        " ev.has_monument_protection)"
    ),
    "    COALESCE(s.hausgeld, iw.hausgeld, bsk.hausgeld, ti.hausgeld) AS hausgeld,\n": (
        "    COALESCE(s.hausgeld, iw.hausgeld, bsk.hausgeld, ev.hausgeld, ti.hausgeld)"
        " AS hausgeld,\n"
    ),
    "LEFT JOIN bsk_exposes bsk ON bsk.listing_id = l.id\n": (
        "LEFT JOIN bsk_exposes bsk ON bsk.listing_id = l.id\n"
        "LEFT JOIN engelvoelkers_exposes ev ON ev.listing_id = l.id\n"
    ),
}


def _bsk_exposes():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0034_bsk_exposes.py")
    spec = importlib.util.spec_from_file_location("bsk_exposes", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _bsk_exposes()._listing_features_sql()
    for old, new in REPLACEMENTS.items():
        if sql.count(old) != 1:
            raise RuntimeError(f"listing_features SQL no longer has one {old.strip()!r}")
        sql = sql.replace(old, new, 1)
    return sql


def _create_view(sql: str) -> None:
    _bsk_exposes()._create_view(sql)


def _create_table() -> None:
    op.create_table(
        "engelvoelkers_exposes",
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("listing_uuid", sa.String(36)),
        sa.Column("display_id", sa.String(64)),
        sa.Column("external_id", sa.String(64)),
        sa.Column("parent_id", sa.String(36)),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("status", sa.String(32)),
        sa.Column("title", sa.Text),
        sa.Column("property_type", sa.Text),
        sa.Column("bezirk", sa.Text),
        sa.Column("ortsteil", sa.Text),
        sa.Column("lat", sa.Float),
        sa.Column("lon", sa.Float),
        sa.Column("price", sa.Float),
        sa.Column("living_area", sa.Float),
        sa.Column("rooms", sa.Float),
        sa.Column("bedrooms", sa.Float),
        sa.Column("bathrooms", sa.Float),
        sa.Column("floor", sa.Integer),
        sa.Column("total_floors", sa.Integer),
        sa.Column("year_built", sa.Integer),
        sa.Column("property_condition", sa.Text),
        sa.Column("heating_type", sa.Text),
        sa.Column("energy_source", sa.Text),
        sa.Column("energy_certificate_type", sa.Text),
        sa.Column("final_energy_type", sa.String(32)),
        sa.Column("final_energy_kwh_m2a", sa.Float),
        sa.Column("energy_efficiency_class", sa.String(8)),
        sa.Column("is_rented", sa.Boolean),
        sa.Column("available_from", sa.Text),
        sa.Column("hausgeld", sa.Float),
        sa.Column("commission_percent", sa.Float),
        sa.Column("buyer_commission", sa.Text),
        sa.Column("flooring", sa.Text),
        sa.Column("balcony_count", sa.Integer),
        sa.Column("has_balcony", sa.Boolean),
        sa.Column("has_terrace", sa.Boolean),
        sa.Column("has_cellar", sa.Boolean),
        sa.Column("has_elevator", sa.Boolean),
        sa.Column("has_parking", sa.Boolean),
        sa.Column("has_fitted_kitchen", sa.Boolean),
        sa.Column("has_garden", sa.Boolean),
        sa.Column("has_guest_toilet", sa.Boolean),
        sa.Column("has_monument_protection", sa.Boolean),
        sa.Column("has_fireplace", sa.Boolean),
        sa.Column("description", sa.Text),
        sa.Column("location_text", sa.Text),
        sa.Column("commission_text", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("photo_count", sa.Integer),
        sa.Column("floor_plan_count", sa.Integer),
        sa.Column("media_dir", sa.Text),
        sa.Column("media", JSONB, nullable=False, server_default="[]"),
        sa.Column("raw", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_engelvoelkers_exposes_listing_uuid", "engelvoelkers_exposes", ["listing_uuid"]
    )


def upgrade() -> None:
    _create_table()
    _create_view(_listing_features_sql())


def downgrade() -> None:
    _create_view(_bsk_exposes()._listing_features_sql())
    op.drop_index("ix_engelvoelkers_exposes_listing_uuid", table_name="engelvoelkers_exposes")
    op.drop_table("engelvoelkers_exposes")
