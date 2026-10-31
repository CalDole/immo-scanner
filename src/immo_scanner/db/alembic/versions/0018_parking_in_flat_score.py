"""Count parking_score inside flat_score, not as its own group

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-05
"""

from collections.abc import Sequence

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _gaps_migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0017_neubaukompass_gaps.py")
    spec = importlib.util.spec_from_file_location("gaps", path)
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    return previous


def upgrade() -> None:
    previous = _gaps_migration()
    previous._rented_migration()._scores_migration()._create_view(previous._listing_features_sql())


def downgrade() -> None:
    # The previous view definition lived in scoring.py; a downgrade cannot restore
    # the old Python, so rebuild from the same SQL that 0017 used at the time.
    previous = _gaps_migration()
    previous._rented_migration()._scores_migration()._create_view(previous._listing_features_sql())
