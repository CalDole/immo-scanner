"""Rented status on listing_features, from the exposé or the listing title

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-04
"""

from collections.abc import Sequence

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AVAILABLE_FROM = (
    "    COALESCE(nbk.available_from, is24.available_from) AS available_from,\n"
)
# ImmoScout24 stores the flag. Other portals only say it in the title.
# "unvermietet" contains "vermietet", so that case has to come first.
RENTED = (
    AVAILABLE_FROM
    + "    COALESCE(\n"
    + "        is24.is_rented,\n"
    + "        CASE\n"
    + "            WHEN l.title ~* 'unvermietet' THEN false\n"
    + "            WHEN l.title ~* 'vermietet' THEN true\n"
    + "        END\n"
    + "    ) AS is_rented,\n"
)


def _scores_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0015_listing_scores.py")
    spec = importlib.util.spec_from_file_location("scores", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _scores_migration()._scored_listing_features_sql()
    if sql.count(AVAILABLE_FROM) != 1:
        raise RuntimeError("listing_features SQL no longer has one available_from column")
    return sql.replace(AVAILABLE_FROM, RENTED, 1)


def upgrade() -> None:
    _scores_migration()._create_view(_listing_features_sql())


def downgrade() -> None:
    previous = _scores_migration()
    previous._create_view(previous._scored_listing_features_sql())
