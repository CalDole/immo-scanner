"""Crime and Berliner Register scores on listing_features

The two scores join the neighbourhood mean. Their source tables are created by
earlier revisions, so the joins are added here rather than in the original score SQL.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-09
"""

from collections.abc import Sequence

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _origin():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0028_origin_neighborhood_scores.py")
    spec = importlib.util.spec_from_file_location("origin_neighborhood_scores", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def _listing_features_sql() -> str:
    from immo_scanner.explore.scoring import with_safety_neighborhood_scores

    return with_safety_neighborhood_scores(_origin()._listing_features_sql())


def upgrade() -> None:
    _origin()._grundschulen()._create_view(_listing_features_sql())


def downgrade() -> None:
    previous = _origin()
    previous._grundschulen()._create_view(previous._listing_features_sql())
