"""The one place the rest of the app gets its data from."""

import logging
from typing import Dict

from sqlalchemy import func, select

from database import Database
from entities import Analysis, LegalReference, User
from repositories import AnalysisRepository, ReferenceRepository, UserRepository

logger = logging.getLogger(__name__)


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
        """Connect, create any missing tables and return how many records are stored."""
        if not self._database.ping():
            raise RuntimeError(f"Cannot connect to the database at {self._database.safe_url}")
        self._database.create_tables()
        counts = self.counts()
        logger.info("Connected to %s: %s", self._database.safe_url, counts)
        return counts

    def counts(self) -> Dict[str, int]:
        tables = (('users', User), ('analyses', Analysis), ('references', LegalReference))
        with self._database.session() as session:
            return {name: session.scalar(select(func.count()).select_from(model))
                    for name, model in tables}

    def close(self) -> None:
        self._database.dispose()
