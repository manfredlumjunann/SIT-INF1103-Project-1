"""Repositories: the only classes that talk to the database about one kind of entity."""

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from typing import Generic, List, Optional, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from database import Database
from entities import (ANALYSIS_ID_SEQUENCE, USER_ID_SEQUENCE, Analysis, AnalysisStatus, Clause,
                      ClauseReference, LegalReference, RiskLevel, Role, User, Verification)

logger = logging.getLogger(__name__)

T = TypeVar('T')


def _format_id(prefix: str, number: int) -> str:
    return f'{prefix}_{number:03d}'


def _violated_constraint(error: IntegrityError) -> Optional[str]:
    diag = getattr(error.orig, 'diag', None)
    return getattr(diag, 'constraint_name', None)


class Repository(ABC, Generic[T]):
    """What every repository can do; each subclass stores one kind of entity."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @abstractmethod
    def get(self, entity_id: str) -> Optional[T]:
        """The entity with this id, or None."""

    @abstractmethod
    def list(self) -> List[T]:
        """Every entity of this kind."""


class UserRepository(Repository[User]):

    def create(self, username: str, password_hash: str, role: Role = Role.CUSTOMER) -> User:
        """Create a user. Usernames are unique regardless of case and stored in lowercase.
        The password must already be hashed by the caller."""
        fields = User.validated_fields(username, password_hash, role)
        taken = ValueError(f"Username already taken: {fields['username']}")
        try:
            with self._database.session() as session:
                # Checked first so a taken name does not use up an id number
                if session.scalar(select(User.id).where(User.username == fields['username'])):
                    raise taken
                number = session.scalar(select(USER_ID_SEQUENCE.next_value()))
                user = User(id=_format_id('us', number), **fields)
                session.add(user)
                session.flush()
                session.refresh(user)  # loads created_at set by the database
                return user
        except IntegrityError as error:
            # Two requests registering the same name at once: the unique index decides
            if _violated_constraint(error) == 'uq_users_username':
                raise taken from None
            raise

    def get(self, entity_id: str) -> Optional[User]:
        with self._database.session() as session:
            return session.get(User, entity_id)

    def find_by_username(self, username: str) -> Optional[User]:
        username = User.normalise_username(username)
        with self._database.session() as session:
            return session.scalar(select(User).where(User.username == username))

    def list(self) -> List[User]:
        """Every user, ordered by username."""
        with self._database.session() as session:
            return list(session.scalars(select(User).order_by(User.username)))


class ReferenceRepository(Repository[LegalReference]):

    def get(self, entity_id: str) -> Optional[LegalReference]:
        with self._database.session() as session:
            return session.get(LegalReference, entity_id)

    def list(self) -> List[LegalReference]:
        with self._database.session() as session:
            return list(session.scalars(select(LegalReference).order_by(LegalReference.id)))

    def add_many(self, references: List[LegalReference]) -> List[LegalReference]:
        """Save references whose id is not stored yet; existing ones are left untouched.
        Returns the references that were added."""
        with self._database.session() as session:
            return self.add_many_in(session, references)

    @staticmethod
    def add_many_in(session: Session, references: List[LegalReference]) -> List[LegalReference]:
        """add_many inside an existing transaction (used by AnalysisRepository.complete)."""
        if not references:
            return []
        rows = [{'id': r.id, 'url': r.url, 'title': r.title, 'domain': r.domain,
                 'verification': r.verification.value} for r in references]
        statement = (pg_insert(LegalReference).values(rows)
                     .on_conflict_do_nothing().returning(LegalReference.id))
        added = set(session.scalars(statement))
        return [r for r in references if r.id in added]

    def set_verification(self, ref_id: str, verification: Verification) -> LegalReference:
        verification = Verification.parse(verification)
        with self._database.session() as session:
            reference = session.get(LegalReference, ref_id)
            if reference is None:
                raise LookupError(f"references record not found: {ref_id}")
            reference.verify(verification)
            session.flush()
            return reference


class AnalysisRepository(Repository[Analysis]):
    # Load clauses and their references up front (a few queries, not one per clause)
    WITH_CLAUSES = (selectinload(Analysis.clauses).selectinload(Clause.references)
                    .selectinload(ClauseReference.reference),)

    def __init__(self, database: Database, references: ReferenceRepository) -> None:
        super().__init__(database)
        self._references = references

    @staticmethod
    def compute_contract_hash(contract_text: str, context: str = '') -> str:
        """Identical contract text AND context produce the same hash, so a repeat
        request can reuse a saved result while a new question forces a rescan."""
        payload = json.dumps([contract_text, context], ensure_ascii=False)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def _require(self, session: Session, analysis_id: str) -> Analysis:
        analysis = session.get(Analysis, analysis_id, options=self.WITH_CLAUSES)
        if analysis is None:
            raise LookupError(f"analyses record not found: {analysis_id}")
        return analysis

    @staticmethod
    def _newest_first(query):
        return query.order_by(Analysis.created_at.desc(), Analysis.id.desc())

    # Writing

    def create(self, owner_id: str, filename: str, contract_text: str, context: str = '') -> Analysis:
        """Record a new analysis in 'processing' state before the AI runs."""
        with self._database.session() as session:
            if session.get(User, owner_id) is None:
                raise LookupError(f"users record not found: {owner_id}")
            number = session.scalar(select(ANALYSIS_ID_SEQUENCE.next_value()))
            analysis = Analysis(
                id=_format_id('an', number), owner_id=owner_id, filename=filename,
                contract_text=contract_text, context=context,
                contract_hash=self.compute_contract_hash(contract_text, context))
            session.add(analysis)
            session.flush()
            session.refresh(analysis, ['created_at'])
            return analysis

    def complete(self, analysis_id: str, ai_clauses: List[dict]) -> Analysis:
        """Store the AI-flagged clauses and mark the analysis done.

        Every clause is validated before anything is written, and the references, clauses
        and status are saved in one transaction.
        """
        with self._database.session() as session:
            analysis = self._require(session, analysis_id)
            new_references: dict = {}
            clauses = [Clause.from_ai(index, raw, new_references)
                       for index, raw in enumerate(ai_clauses, start=1)]
            self._references.add_many_in(session, list(new_references.values()))

            # Replace any earlier clauses; flush the deletes first so positions can be reused
            analysis.discard_clauses()
            session.flush()
            analysis.mark_done(clauses)
        return self.get(analysis_id)

    def fail(self, analysis_id: str) -> Analysis:
        with self._database.session() as session:
            analysis = self._require(session, analysis_id)
            analysis.mark_failed()
            session.flush()
            return analysis

    def delete(self, analysis_id: str) -> Analysis:
        """Permanently remove an analysis. Returns the deleted analysis.
        Shared legal references are kept because other analyses may cite them."""
        with self._database.session() as session:
            analysis = self._require(session, analysis_id)
            session.delete(analysis)
            return analysis

    # Reading

    def get(self, entity_id: str) -> Optional[Analysis]:
        with self._database.session() as session:
            return session.get(Analysis, entity_id, options=self.WITH_CLAUSES)

    def find_by_hash(self, owner_id: str, contract_hash: str) -> Optional[Analysis]:
        query = self._newest_first(
            select(Analysis).options(*self.WITH_CLAUSES)
            .where(Analysis.owner_id == owner_id,
                   Analysis.contract_hash == contract_hash,
                   Analysis.has_status(AnalysisStatus.DONE))).limit(1)
        with self._database.session() as session:
            return session.scalar(query)

    def list(self, owner_id: Optional[str] = None,
             status: Optional[AnalysisStatus] = None,
             risk_level: Optional[RiskLevel] = None) -> List[Analysis]:
        """Newest first. risk_level keeps only analyses with at least one clause at that level."""
        query = select(Analysis).options(*self.WITH_CLAUSES)
        if status is not None:
            query = query.where(Analysis.has_status(status))
        if risk_level is not None:
            query = query.where(Analysis.clauses.any(Clause.has_risk_level(risk_level)))
        if owner_id is not None:
            query = query.where(Analysis.owner_id == owner_id)
        with self._database.session() as session:
            return list(session.scalars(self._newest_first(query)))
