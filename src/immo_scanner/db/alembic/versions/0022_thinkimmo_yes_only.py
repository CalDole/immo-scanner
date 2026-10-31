"""Use ThinkImmo's yes/no facts only when they say yes

ThinkImmo reports facts an ad doesn't state as false: 1,794 of 2,535 properties had
no elevator, and every property was unrented although 6 of the 18 linked portal
exposés stating it said rented. Its elevator, cellar, balcony, fitted kitchen and
rented flags now fill listing_features gaps only when true, and the rented flag
comes after the title keywords.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-05
"""

from collections.abc import Sequence

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPLACEMENTS = {
    "ka.has_elevator, ti.has_elevator)": "ka.has_elevator, NULLIF(ti.has_elevator, false))",
    "is24.has_cellar, ti.has_cellar)": "is24.has_cellar, NULLIF(ti.has_cellar, false))",
    "is24.has_balcony_terrace, ti.has_balcony)": (
        "is24.has_balcony_terrace, NULLIF(ti.has_balcony, false))"
    ),
    "ka.has_fitted_kitchen, ti.has_fitted_kitchen,": (
        "ka.has_fitted_kitchen, NULLIF(ti.has_fitted_kitchen, false),"
    ),
    "        is24.is_rented,\n        ti.is_rented,\n": "        is24.is_rented,\n",
    "            WHEN l.title ~* 'vermietet' THEN true\n        END\n    ) AS is_rented,\n": (
        "            WHEN l.title ~* 'vermietet' THEN true\n"
        "        END,\n"
        "        NULLIF(ti.is_rented, false)\n"
        "    ) AS is_rented,\n"
    ),
}


def _listings_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0021_thinkimmo_listings.py")
    spec = importlib.util.spec_from_file_location("thinkimmo_listings", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _listings_migration()._listing_features_sql()
    for old, new in REPLACEMENTS.items():
        if sql.count(old) != 1:
            raise RuntimeError(f"listing_features SQL no longer has one copy of {old!r}")
        sql = sql.replace(old, new, 1)
    return sql


def upgrade() -> None:
    _listings_migration()._create_view(_listing_features_sql())


def downgrade() -> None:
    previous = _listings_migration()
    previous._create_view(previous._listing_features_sql())
