"""Data Manager: the single entry point to storage for the rest of the application."""

import logging
from pathlib import Path
from typing import Dict, Optional

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import func, select

from database import Database
from entities import Analysis, LegalReference, User
from repositories import AnalysisRepository, ReferenceRepository, UserRepository

logger = logging.getLogger(__name__)

ALEMBIC_INI = Path(__file__).with_name('alembic.ini')


def expected_schema_revision() -> Optional[str]:
    """The newest migration shipped with this code, or None if the migration files
    are not available (e.g. this folder copied somewhere on its own)."""
    if not ALEMBIC_INI.exists():
        return None
    return ScriptDirectory.from_config(AlembicConfig(str(ALEMBIC_INI))).get_current_head()


class DataManager:
    """Owns the database connection and creates the repositories that share it."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._users = UserRepository(database)
        self._references = ReferenceRepository(database)
        self._analyses = AnalysisRepository(database, self._references)

    @classmethod
    def from_env(cls) -> 'DataManager':
        """Connect using the DATABASE_URL environment variable."""
        return cls(Database.from_env())

    @property
    def users(self) -> UserRepository:
        return self._users

    @property
    def references(self) -> ReferenceRepository:
        return self._references

    @property
    def analyses(self) -> AnalysisRepository:
        return self._analyses

    def init_storage(self) -> Dict[str, int]:
        """Check the database is reachable and fully migrated. Returns record counts."""
        if not self._database.ping():
            raise RuntimeError(f"Cannot connect to the database at {self._database.safe_url}")
        current = self._database.schema_revision()
        expected = expected_schema_revision()
        if current is None or (expected is not None and current != expected):
            raise RuntimeError(
                f"Database schema is at revision {current or '(none)'} but this code expects "
                f"{expected or 'a migrated schema'}. Run: alembic upgrade head")
        counts = self.counts()
        logger.info("Connected to %s (schema %s): %s", self._database.safe_url, current, counts)
        return counts

    def counts(self) -> Dict[str, int]:
        tables = (('users', User), ('analyses', Analysis), ('references', LegalReference))
        with self._database.session() as session:
            return {name: session.scalar(select(func.count()).select_from(model))
                    for name, model in tables}

    def close(self) -> None:
        self._database.dispose()
