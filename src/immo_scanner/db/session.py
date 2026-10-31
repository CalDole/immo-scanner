from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from immo_scanner.config import get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url.strip()
    if not url:
        raise RuntimeError(
            "Database URL is missing. Set DATABASE_URL or [postgres] in "
            ".streamlit/secrets.toml, or IMMO_DATABASE_URL in .env."
        )
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
