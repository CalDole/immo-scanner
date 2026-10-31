"""Full ImmoScout24 exposé copies

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "immoscout24_exposes",
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scout_id", sa.String(32), index=True),
        sa.Column("object_id", sa.Text),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("address", sa.Text),
        sa.Column("property_type", sa.Text),
        sa.Column("is_rented", sa.Boolean),
        sa.Column("living_area", sa.Float),
        sa.Column("usable_area", sa.Float),
        sa.Column("available_from", sa.Text),
        sa.Column("rooms", sa.Float),
        sa.Column("bedrooms", sa.Float),
        sa.Column("bathrooms", sa.Float),
        sa.Column("price", sa.Float),
        sa.Column("price_per_m2", sa.Float),
        sa.Column("buyer_commission", sa.Text),
        sa.Column("year_built", sa.Integer),
        sa.Column("heating_type", sa.Text),
        sa.Column("energy_source", sa.Text),
        sa.Column("energy_certificate", sa.Text),
        sa.Column("energy_certificate_type", sa.Text),
        sa.Column("final_energy_kwh_m2a", sa.Float),
        sa.Column("energy_efficiency_class", sa.String(8)),
        sa.Column("energy_certificate_year_built", sa.Integer),
        sa.Column("has_balcony_terrace", sa.Boolean),
        sa.Column("has_terrace", sa.Boolean),
        sa.Column("has_cellar", sa.Boolean),
        sa.Column("internet_speed", sa.Text),
        sa.Column("internet_mbit", sa.Integer),
        sa.Column("description", sa.Text),
        sa.Column("equipment_text", sa.Text),
        sa.Column("other_text", sa.Text),
        sa.Column("location_text", sa.Text),
        sa.Column("photo_count", sa.Integer),
        sa.Column("floor_plan_count", sa.Integer),
        sa.Column("media_dir", sa.Text),
        sa.Column("media", JSONB, nullable=False, server_default="[]"),
        sa.Column("facts", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("immoscout24_exposes")
