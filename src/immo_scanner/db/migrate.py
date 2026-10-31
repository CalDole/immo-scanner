from pathlib import Path

from alembic import command
from alembic.config import Config

from immo_scanner.config import get_settings

ALEMBIC_DIR = Path(__file__).parent / "alembic"


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return config


def upgrade(revision: str = "head") -> None:
    command.upgrade(alembic_config(), revision)
