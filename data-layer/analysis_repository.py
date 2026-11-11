import hashlib
import json
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from analysis import Analysis
from analysis_status import AnalysisStatus
from orm_base import ANALYSIS_ID_SEQUENCE
from clause import Clause
from clause_reference import ClauseReference
from database import Database
from reference_repository import ReferenceRepository
from repository import Repository, format_id
from risk_level import RiskLevel
from user import User


class AnalysisRepository(Repository[Analysis]):
    # Load clauses, their references and comments up front (a few queries, not one per clause)
    WITH_CLAUSES = (
        selectinload(Analysis.clauses).selectinload(Clause.references).selectinload(ClauseReference.reference),
        selectinload(Analysis.clauses).selectinload(Clause.comment),
    )

    def __init__(self, database: Database, references: ReferenceRepository) -> None:
        super().__init__(database)
        self._references = references

    # Identical contract text AND context produce the same hash, so a repeat
    # request can reuse a saved result while a new question forces a rescan.
    @staticmethod
    def compute_contract_hash(contract_text: str, context: str = '') -> str:
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

    # Record a new analysis in 'processing' state before the AI runs.
    def create(self, owner_id: str, filename: str, contract_text: str, context: str = '') -> Analysis:
        with self._database.session() as session:
            if session.get(User, owner_id) is None:
                raise LookupError(f"users record not found: {owner_id}")
            number = session.scalar(select(ANALYSIS_ID_SEQUENCE.next_value()))
            analysis = Analysis(
                id=format_id('an', number), owner_id=owner_id, filename=filename,
                contract_text=contract_text, context=context,
                contract_hash=self.compute_contract_hash(contract_text, context))
            session.add(analysis)
            session.flush()
            session.refresh(analysis, ['created_at'])
            return analysis

    # Store the AI-flagged clauses and mark the analysis done.
    #
    # Every clause is validated before anything is written, and the references, clauses
    # and status are saved in one transaction.
    def complete(self, analysis_id: str, ai_clauses: List[dict]) -> Analysis:
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

    # Permanently remove an analysis. Returns the deleted analysis.
    # Shared legal references are kept because other analyses may cite them.
    def delete(self, analysis_id: str) -> Analysis:
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

    # Newest first. risk_level keeps only analyses with at least one clause at that level.
    def list(self, owner_id: Optional[str] = None,
             status: Optional[AnalysisStatus] = None,
             risk_level: Optional[RiskLevel] = None) -> List[Analysis]:
        query = select(Analysis).options(*self.WITH_CLAUSES)
        if status is not None:
            query = query.where(Analysis.has_status(status))
        if risk_level is not None:
            query = query.where(Analysis.clauses.any(Clause.has_risk_level(risk_level)))
        if owner_id is not None:
            query = query.where(Analysis.owner_id == owner_id)
        with self._database.session() as session:
            return list(session.scalars(self._newest_first(query)))
