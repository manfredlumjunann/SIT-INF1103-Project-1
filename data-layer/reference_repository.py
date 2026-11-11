from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from legal_reference import LegalReference
from repository import Repository
from verification import Verification


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
            reference.verification = verification
            session.flush()
            return reference
