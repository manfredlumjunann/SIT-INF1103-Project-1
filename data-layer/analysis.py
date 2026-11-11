from datetime import datetime
from typing import Dict, List

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from analysis_status import AnalysisStatus
from orm_base import Base
from clause import Clause
from entity_helpers import in_list, iso_utc
from risk_level import RiskLevel


class Analysis(Base):
    __tablename__ = 'analyses'
    __table_args__ = (
        CheckConstraint(in_list('status', AnalysisStatus), name='status'),
        # Cache lookup (same owner + same contract and context); also serves owner-only lookups
        Index('ix_analyses_owner_id_contract_hash', 'owner_id', 'contract_hash'),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    _status: Mapped[str] = mapped_column('status', Text, server_default=AnalysisStatus.PROCESSING.value)
    filename: Mapped[str] = mapped_column(Text, server_default='')
    context: Mapped[str] = mapped_column(Text, server_default='')
    contract_text: Mapped[str] = mapped_column(Text)
    contract_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    clauses: Mapped[List[Clause]] = relationship(
        back_populates='analysis', order_by='Clause.position',
        cascade='all, delete-orphan', passive_deletes=True)

    def __init__(self, id: str, owner_id: str, filename: str, contract_text: str,
                 context: str, contract_hash: str) -> None:
        self.id = id
        self.owner_id = owner_id
        self._status = AnalysisStatus.PROCESSING.value
        self.filename = str(filename or '')
        self.context = str(context or '')
        self.contract_text = contract_text
        self.contract_hash = contract_hash
        self.clauses = []

    @property
    def status(self) -> AnalysisStatus:
        return AnalysisStatus(self._status)

    @property
    def created_at_iso(self) -> str:
        return iso_utc(self.created_at)

    # SQL condition: the analysis is in this status.
    @classmethod
    def has_status(cls, status: AnalysisStatus):
        return cls._status == AnalysisStatus(status).value

    def mark_done(self, clauses: List[Clause]) -> None:
        self.clauses.extend(clauses)
        self._status = AnalysisStatus.DONE.value

    def mark_failed(self) -> None:
        self._status = AnalysisStatus.FAILED.value

    def discard_clauses(self) -> None:
        self.clauses.clear()

    # Clauses per risk level, worked out from the clauses so it cannot go stale.
    def counts(self) -> Dict[RiskLevel, int]:
        counts = {level: 0 for level in RiskLevel}
        for clause in self.clauses:
            counts[clause.risk_level] += 1
        return counts

    def is_owned_by(self, user_id: str) -> bool:
        return self.owner_id == user_id

    def commented_clause_count(self) -> int:
        return sum(1 for clause in self.clauses if clause.comment is not None)
