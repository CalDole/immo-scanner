"""ThinkImmo properties

A standalone table for ThinkImmo's API records: portal facts, ThinkImmo's market
value and rent estimates, cash flow, macro and micro location. Not linked to
`listings`.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT = ("url", "platform_names", "title", "description", "realtor_company")
LOCATION_TEXT = ("city", "borough", "suburb", "street", "house_number")
FLOATS = (
    "buying_price",
    "price_per_m2",
    "living_area",
    "rooms",
    "commission_pct",
    "hausgeld",
)
VALUATION_FLOATS = (
    "lat",
    "lon",
    "market_price_per_m2",
    "market_price_per_m2_min",
    "market_price_per_m2_max",
    "price_to_market_pct",
    "estimated_rent",
    "estimated_rent_per_m2",
    "estimated_rent_per_m2_min",
    "estimated_rent_per_m2_max",
    "current_rent",
    "current_rent_per_m2",
    "gross_return",
    "gross_return_current",
    "cash_flow",
    "own_capital_return",
    "location_score",
    "district_price_per_m2",
    "city_price_per_m2",
    "similar_price_per_m2",
)
INTEGERS = (
    "running_time_days",
    "year_built",
    "last_renovation_year",
    "floor",
    "total_floors",
    "parking_spaces",
    "views",
    "clicks",
    "favorites",
    "image_count",
    "floor_plan_count",
)
BOOLEANS = (
    "is_private_offer",
    "is_active",
    "price_reduced",
    "price_increased",
    "has_elevator",
    "has_cellar",
    "has_balcony",
    "has_garden",
    "has_fitted_kitchen",
    "has_energy_certificate",
    "is_rented",
    "is_leasehold",
    "is_foreclosure",
)
ENUMS = {
    "primary_platform": 64,
    "apartment_type": 32,
    "condition": 48,
    "interior_quality": 32,
    "heating_type": 48,
    "energy_efficiency_class": 16,
}
JSON_LISTS = ("platforms", "images", "buying_price_history", "change_log")
JSON_OBJECTS = (
    "address",
    "original_address",
    "portal_addresses",
    "location_factor",
    "aggregations",
    "offer_prices",
    "cash_flow_calc",
    "raw",
)


def upgrade() -> None:
    op.create_table(
        "thinkimmo_properties",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("detail_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("postcode", sa.String(5)),
        *(sa.Column(name, sa.Text) for name in TEXT + LOCATION_TEXT),
        *(sa.Column(name, sa.String(length)) for name, length in ENUMS.items()),
        *(sa.Column(name, sa.Float) for name in FLOATS + VALUATION_FLOATS),
        *(sa.Column(name, sa.Integer) for name in INTEGERS),
        *(sa.Column(name, sa.Boolean) for name in BOOLEANS),
        *(sa.Column(name, JSONB, nullable=False, server_default="[]") for name in JSON_LISTS),
        *(sa.Column(name, JSONB, nullable=False, server_default="{}") for name in JSON_OBJECTS),
    )
    for column in ("last_seen", "status", "published_at", "postcode"):
        op.create_index(f"ix_thinkimmo_properties_{column}", "thinkimmo_properties", [column])


def downgrade() -> None:
    op.drop_table("thinkimmo_properties")
