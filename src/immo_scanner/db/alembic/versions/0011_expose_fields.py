"""Exposé rooms, amenities and provision from every portal on listing_features

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kleinanzeigen was the only exposé joined into the view; bedrooms, bathrooms, the
# apartment type and the provision therefore stayed empty for ImmoScout24 and
# Neubaukompass even though those tables already hold them.
KA_ONLY = """    d.has_elevator,
    d.has_parking,
    d.has_cellar,
    d.has_balcony,
    d.balcony_count,
    ka.bedrooms,
    ka.bathrooms,
    ka.apartment_type,
    ka.provision,
    ka.posted_on,
    ka.has_terrace,
    ka.has_fitted_kitchen,
"""
EXPOSE_COLUMNS = """    COALESCE(d.has_elevator, ka.has_elevator) AS has_elevator,
    d.has_parking,
    COALESCE(d.has_cellar, is24.has_cellar) AS has_cellar,
    COALESCE(d.has_balcony, ka.has_balcony, nbk.has_balcony_terrace,
             is24.has_balcony_terrace) AS has_balcony,
    d.balcony_count,
    COALESCE(ka.bedrooms, nbk.bedrooms, is24.bedrooms) AS bedrooms,
    COALESCE(ka.bathrooms, nbk.bathrooms, is24.bathrooms) AS bathrooms,
    COALESCE(ka.apartment_type, nbk.unit_type, is24.property_type) AS apartment_type,
    COALESCE(ka.provision, nbk.buyer_commission, is24.buyer_commission) AS provision,
    ka.posted_on,
    COALESCE(ka.has_terrace, is24.has_terrace) AS has_terrace,
    ka.has_fitted_kitchen,
    COALESCE(nbk.available_from, is24.available_from) AS available_from,
"""
KA_JOIN = (
    "LEFT JOIN listing_details d ON d.listing_id = l.id\n"
    "LEFT JOIN kleinanzeigen_ads ka ON ka.listing_id = l.id\n"
)
EXPOSE_JOINS = (
    "LEFT JOIN listing_details d ON d.listing_id = l.id\n"
    "LEFT JOIN kleinanzeigen_ads ka ON ka.listing_id = l.id\n"
    "LEFT JOIN neubaukompass_units nbk ON nbk.listing_id = l.id\n"
    "LEFT JOIN immoscout24_exposes is24 ON is24.listing_id = l.id\n"
)


def _listing_features_sql() -> str:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0010_kleinanzeigen_ads.py")
    spec = importlib.util.spec_from_file_location("ka", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    sql = previous._listing_features_sql()
    if KA_ONLY not in sql or KA_JOIN not in sql:
        raise RuntimeError("listing_features SQL no longer matches the exposé-fields migration")
    return sql.replace(KA_ONLY, EXPOSE_COLUMNS, 1).replace(KA_JOIN, EXPOSE_JOINS, 1)


def _create_view(sql: str) -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS listing_features")
    op.execute(sql)
    op.execute("CREATE UNIQUE INDEX ix_listing_features_id ON listing_features (id)")
    op.execute("CREATE INDEX ix_listing_features_geom ON listing_features USING gist (geom)")
    op.execute("REFRESH MATERIALIZED VIEW listing_features")


def upgrade() -> None:
    _create_view(_listing_features_sql())


def downgrade() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0010_kleinanzeigen_ads.py")
    spec = importlib.util.spec_from_file_location("ka", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    _create_view(previous._listing_features_sql())
