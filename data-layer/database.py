"""PostgreSQL connection handling."""

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from entities import Base

logger = logging.getLogger(__name__)


class Database:
    """Owns the SQLAlchemy engine for one database and hands out sessions.
    Creating a Database does not connect; the first query does."""

    CONNECT_TIMEOUT_SECONDS = 5

    def __init__(self, url: str, echo: bool = False) -> None:
        self._url = self.normalise_url(url)
        self._engine: Engine = create_engine(
            self._url, echo=echo, pool_pre_ping=True,
            connect_args={'connect_timeout': self.CONNECT_TIMEOUT_SECONDS})
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    @classmethod
    def from_env(cls, variable: str = 'DATABASE_URL') -> 'Database':
        url = os.getenv(variable)
        if not url:
            raise RuntimeError(f"{variable} is not set")
        return cls(url)

    @staticmethod
    def normalise_url(url: str) -> str:
        """Use the psycopg 3 driver for plain postgres:// or postgresql:// URLs,
        which is the form hosted providers usually give out."""
        url = url.strip()
        for prefix in ('postgres://', 'postgresql://'):
            if url.startswith(prefix):
                return 'postgresql+psycopg://' + url[len(prefix):]
        return url

    @property
    def safe_url(self) -> str:
        return make_url(self._url).render_as_string(hide_password=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self._engine.connect() as connection:
                connection.execute(text('SELECT 1'))
            return True
        except SQLAlchemyError as error:
            logger.warning("Database %s is not reachable: %s", self.safe_url, error)
            return False

    def create_tables(self) -> None:
        """Create the tables, and the id sequences, that do not exist yet. Existing tables are left as they are."""
        Base.metadata.create_all(self._engine)

    def dispose(self) -> None:
        """Close every pooled connection (e.g. at shutdown or between tests)."""
        self._engine.dispose()

    def __repr__(self) -> str:
        return f'Database({self.safe_url!r})'
