"""Read Volt's AGH share under the key the election loader stores

The loader keys Volt as voltdeutschland, so the tolerance score read no Volt share
from 'volt' for any listing.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-06
"""

from collections.abc import Sequence

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VOLT_LINE = "            NULLIF(base.agh_party_shares ->> 'voltdeutschland', '')::float,\n"


def _yes_only_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0022_thinkimmo_yes_only.py")
    spec = importlib.util.spec_from_file_location("thinkimmo_yes_only", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _yes_only_migration()._listing_features_sql()
    if sql.count(VOLT_LINE) != 1:
        raise RuntimeError("listing_features SQL no longer has one voltdeutschland read")
    return sql


def upgrade() -> None:
    _yes_only_migration()._listings_migration()._create_view(_listing_features_sql())


def downgrade() -> None:
    previous = _yes_only_migration()
    previous._listings_migration()._create_view(_listing_features_sql().replace(VOLT_LINE, ""))
