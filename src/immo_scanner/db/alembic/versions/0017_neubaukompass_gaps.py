"""Fill Neubaukompass gaps the exposé already states

Ground floor is written "EG", so those floors were dropped. The energy carrier,
a "Nein" provision and Einbauküche stayed on the unit row and never reached
listing_features. Garage lines stayed in the facts JSON.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLOOR = "    COALESCE(d.floor, s.floor) AS floor,\n"
FLOOR_WITH_UNIT = "    COALESCE(d.floor, nbk.floor, s.floor) AS floor,\n"
ENERGY = "    d.energy_source,\n"
ENERGY_WITH_UNIT = (
    "    COALESCE(d.energy_source, nbk.energy_source, is24.energy_source) AS energy_source,\n"
)
KITCHEN = "    ka.has_fitted_kitchen,\n"
KITCHEN_WITH_UNIT = (
    "    COALESCE(ka.has_fitted_kitchen,"
    " CASE WHEN jsonb_exists(nbk.features, 'Einbauküche') THEN true END)"
    " AS has_fitted_kitchen,\n"
)
PROVISION = (
    "    COALESCE(ka.provision, nbk.buyer_commission, is24.buyer_commission) AS provision,\n"
)
PROVISION_WITH_UNIT = (
    "    COALESCE(ka.provision, nbk.buyer_commission,"
    " CASE WHEN nbk.has_commission IS FALSE THEN 'Nein' END,"
    " is24.buyer_commission) AS provision,\n"
)


def _rented_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0016_is_rented.py")
    spec = importlib.util.spec_from_file_location("rented", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    sql = _rented_migration()._listing_features_sql()
    for old in (FLOOR, ENERGY, KITCHEN, PROVISION):
        if sql.count(old) != 1:
            raise RuntimeError(f"listing_features SQL no longer has one copy of {old!r}")
    return (
        sql.replace(FLOOR, FLOOR_WITH_UNIT, 1)
        .replace(ENERGY, ENERGY_WITH_UNIT, 1)
        .replace(KITCHEN, KITCHEN_WITH_UNIT, 1)
        .replace(PROVISION, PROVISION_WITH_UNIT, 1)
    )


def _backfill_units() -> None:
    op.execute(
        """
        UPDATE neubaukompass_units
        SET floor = 0
        WHERE floor IS NULL
          AND lower(btrim(facts->>'Etage')) IN ('eg', 'eg.', 'erdgeschoss', 'hochparterre')
        """
    )
    op.execute(
        """
        UPDATE listing_details AS d
        SET floor = u.floor
        FROM neubaukompass_units AS u
        WHERE d.listing_id = u.listing_id
          AND d.floor IS NULL
          AND u.floor = 0
          AND lower(btrim(u.facts->>'Etage')) IN ('eg', 'eg.', 'erdgeschoss', 'hochparterre')
        """
    )
    op.execute(
        """
        UPDATE listing_details AS d
        SET energy_source = u.energy_source
        FROM neubaukompass_units AS u
        WHERE d.listing_id = u.listing_id
          AND d.energy_source IS NULL
          AND u.energy_source IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE listing_details AS d
        SET has_parking = true
        FROM neubaukompass_units AS u
        WHERE d.listing_id = u.listing_id
          AND d.has_parking IS NULL
          AND (
            (
              COALESCE(u.facts->>'Garage/ Stellplatz', '') <> ''
              AND u.facts->>'Garage/ Stellplatz' !~* '^(kein|keine|keinen|nein|ohne)([^[:alnum:]_]|$)'
            )
            OR COALESCE(u.facts->>'Stellplatzpreis', '') <> ''
          )
        """
    )


def upgrade() -> None:
    _backfill_units()
    _rented_migration()._scores_migration()._create_view(_listing_features_sql())


def downgrade() -> None:
    previous = _rented_migration()
    previous._scores_migration()._create_view(previous._listing_features_sql())
    op.execute(
        """
        UPDATE listing_details AS d
        SET floor = NULL
        FROM neubaukompass_units AS u
        WHERE d.listing_id = u.listing_id
          AND d.floor = 0
          AND lower(btrim(u.facts->>'Etage')) IN ('eg', 'eg.', 'erdgeschoss', 'hochparterre')
        """
    )
    op.execute(
        """
        UPDATE neubaukompass_units
        SET floor = NULL
        WHERE floor = 0
          AND lower(btrim(facts->>'Etage')) IN ('eg', 'eg.', 'erdgeschoss', 'hochparterre')
        """
    )
