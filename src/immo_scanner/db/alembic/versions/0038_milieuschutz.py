"""Milieuschutz street list and listing status

listings.milieuschutz is Yes, Closeby or No. The street rows live in
milieuschutz_streets and are filled by `immo load-reference milieuschutz`.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADDRESS = "    l.address,\n"
_ADDRESS_WITH_STATUS = "    l.address,\n    l.milieuschutz,\n"


def _school_type():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0037_school_type_score.py")
    spec = importlib.util.spec_from_file_location("school_type_score", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _school_type()._listing_features_sql()
    if sql.count(_ADDRESS) != 1:
        raise RuntimeError("listing_features SQL no longer has one listing address column")
    return sql.replace(_ADDRESS, _ADDRESS_WITH_STATUS, 1)


def _create_table() -> None:
    op.create_table(
        "milieuschutz_streets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("street", sa.Text, nullable=False),
        sa.Column("street_key", sa.String(200), nullable=False),
        sa.Column("bezirk", sa.Text, nullable=False),
        sa.Column("gebiet", sa.Text),
        sa.Column("covers_all", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ranges", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raw_numbers", sa.Text),
    )
    op.create_index("ix_milieuschutz_streets_street_key", "milieuschutz_streets", ["street_key"])


def upgrade() -> None:
    _create_table()
    op.add_column("listings", sa.Column("milieuschutz", sa.String(16)))
    _school_type()._engelvoelkers()._create_view(_listing_features_sql())


def downgrade() -> None:
    previous = _school_type()
    previous._engelvoelkers()._create_view(previous._listing_features_sql())
    op.drop_column("listings", "milieuschutz")
    op.drop_index("ix_milieuschutz_streets_street_key", table_name="milieuschutz_streets")
    op.drop_table("milieuschutz_streets")
