# What every repository can do; each subclass stores one kind of entity.

from abc import ABC, abstractmethod
from typing import Generic, List, Optional, TypeVar

from sqlalchemy.exc import IntegrityError

from database import Database

T = TypeVar('T')


def format_id(prefix: str, number: int) -> str:
    return f'{prefix}_{number:03d}'


def violated_constraint(error: IntegrityError) -> Optional[str]:
    diag = getattr(error.orig, 'diag', None)
    return getattr(diag, 'constraint_name', None)


# Everything that reads or writes the database goes through one repository per kind of entity.
class Repository(ABC, Generic[T]):

    def __init__(self, database: Database) -> None:
        self._database = database

    # The entity with this id, or None.
    @abstractmethod
    def get(self, entity_id: str) -> Optional[T]:
        pass

    # Every entity of this kind.
    @abstractmethod
    def list(self) -> List[T]:
        pass
