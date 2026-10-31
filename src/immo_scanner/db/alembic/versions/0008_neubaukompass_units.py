"""Per-flat Neubaukompass exposés; drop the old project-level listings

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "neubaukompass_units",
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unit_id", sa.String(32), index=True),
        sa.Column("object_number", sa.Text),
        sa.Column("project_id", sa.String(32), index=True),
        sa.Column("project_name", sa.Text),
        sa.Column("project_url", sa.Text),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("address", sa.Text),
        sa.Column("unit_type", sa.Text),
        sa.Column("price", sa.Float),
        sa.Column("price_per_m2", sa.Float),
        sa.Column("floor", sa.Integer),
        sa.Column("total_floors", sa.Integer),
        sa.Column("rooms", sa.Float),
        sa.Column("bedrooms", sa.Float),
        sa.Column("bathrooms", sa.Float),
        sa.Column("living_area", sa.Float),
        sa.Column("year_built", sa.Integer),
        sa.Column("last_renovation_year", sa.Integer),
        sa.Column("available_from", sa.Text),
        sa.Column("has_commission", sa.Boolean),
        sa.Column("buyer_commission", sa.Text),
        sa.Column("seller_commission", sa.Text),
        sa.Column("energy_certificate_type", sa.Text),
        sa.Column("energy_source", sa.Text),
        sa.Column("energy_certificate_date", sa.Text),
        sa.Column("energy_efficiency_class", sa.String(8)),
        sa.Column("final_energy_kwh_m2a", sa.Float),
        sa.Column("has_balcony_terrace", sa.Boolean),
        sa.Column("features", JSONB, nullable=False, server_default="[]"),
        sa.Column("location_text", sa.Text),
        sa.Column("equipment_text", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("floor_plan_url", sa.Text),
        sa.Column("energy_certificate_url", sa.Text),
        sa.Column("website_url", sa.Text),
        sa.Column("virtual_tour_url", sa.Text),
        sa.Column("provider_name", sa.Text),
        sa.Column("provider_address", sa.Text),
        sa.Column("contact_name", sa.Text),
        sa.Column("contact_phone", sa.Text),
        sa.Column("contact_email", sa.Text),
        sa.Column("photo_count", sa.Integer),
        sa.Column("floor_plan_count", sa.Integer),
        sa.Column("media_dir", sa.Text),
        sa.Column("media", JSONB, nullable=False, server_default="[]"),
        sa.Column("facts", JSONB, nullable=False, server_default="{}"),
    )
    # Flats replace projects as listings, and their external_id is the exposé id rather
    # than the project slug ("127686.html"), so the old rows can never be matched again.
    op.execute("DELETE FROM listings WHERE source = 'neubaukompass' AND external_id LIKE '%.html'")
    op.execute("REFRESH MATERIALIZED VIEW listing_features")


def downgrade() -> None:
    op.drop_table("neubaukompass_units")
    op.execute("REFRESH MATERIALIZED VIEW listing_features")
