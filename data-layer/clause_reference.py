from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orm_base import Base
from legal_reference import LegalReference


# Links a clause to a legal reference, with the clause-specific snippet.
# Shared references survive when an analysis (and its clauses) is deleted.
class ClauseReference(Base):
    __tablename__ = 'clause_references'
    __table_args__ = (
        CheckConstraint('position >= 1', name='position_positive'),
    )

    clause_id: Mapped[int] = mapped_column(
        ForeignKey('clauses.id', ondelete='CASCADE'), primary_key=True)
    reference_id: Mapped[str] = mapped_column(
        ForeignKey('legal_references.id'), primary_key=True, index=True)
    position: Mapped[int] = mapped_column(Integer)
    snippet: Mapped[str] = mapped_column(Text, server_default='')

    clause: Mapped['Clause'] = relationship(back_populates='references')
    reference: Mapped[LegalReference] = relationship()

    def __init__(self, position: int, reference_id: str, snippet: str = '') -> None:
        self.position = position
        self.reference_id = reference_id
        self.snippet = snippet
