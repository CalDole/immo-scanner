"""Origin diversity, citizenship and OIC scores on listing_features

The three scores join education and tolerance in the neighbourhood mean.
Their source tables are created by earlier revisions, so the joins are added
here rather than in the original score SQL.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-08
"""

from collections.abc import Sequence

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _grundschulen():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0026_grundschulen.py")
    spec = importlib.util.spec_from_file_location("grundschulen", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    from immo_scanner.explore.scoring import with_origin_neighborhood_scores

    return with_origin_neighborhood_scores(_grundschulen()._listing_features_sql())


def upgrade() -> None:
    _grundschulen()._create_view(_listing_features_sql())


def downgrade() -> None:
    previous = _grundschulen()
    previous._create_view(previous._listing_features_sql())
