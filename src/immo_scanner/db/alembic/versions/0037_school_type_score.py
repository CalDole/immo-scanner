"""Schultypisierung score on listing_features

The score joins the neighbourhood mean. It reads the Planungsraum indicator
already stored in area_indicators.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-13
"""

from collections.abc import Sequence

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _engelvoelkers():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0036_engelvoelkers_exposes.py")
    spec = importlib.util.spec_from_file_location("engelvoelkers_exposes", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    from immo_scanner.explore.scoring import with_school_type_neighborhood_score

    return with_school_type_neighborhood_score(_engelvoelkers()._listing_features_sql())


def upgrade() -> None:
    _engelvoelkers()._create_view(_listing_features_sql())


def downgrade() -> None:
    previous = _engelvoelkers()
    previous._create_view(previous._listing_features_sql())
