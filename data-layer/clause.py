import re
from typing import Dict, List, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clause_comment import ClauseComment
from clause_reference import ClauseReference
from entity_helpers import in_list
from legal_reference import LegalReference
from orm_base import Base
from risk_level import RiskLevel


class Clause(Base):
    __tablename__ = 'clauses'
    __table_args__ = (
        UniqueConstraint('analysis_id', 'position', name='uq_clauses_analysis_id_position'),
        CheckConstraint(in_list('risk_level', RiskLevel), name='risk_level'),
        CheckConstraint('position >= 1', name='position_positive'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey('analyses.id', ondelete='CASCADE'))
    position: Mapped[int] = mapped_column(Integer)  # 1-based order; shown as cl_<position>
    clause_type: Mapped[str] = mapped_column(Text, server_default='Unspecified')
    _risk_level: Mapped[str] = mapped_column('risk_level', Text)
    clause_text: Mapped[str] = mapped_column(Text, server_default='')
    location_page: Mapped[Optional[int]] = mapped_column(Integer)
    location_clause_ref: Mapped[Optional[str]] = mapped_column(Text)
    location_raw: Mapped[Optional[str]] = mapped_column(Text)
    issue_description: Mapped[str] = mapped_column(Text, server_default='')
    workaround: Mapped[str] = mapped_column(Text, server_default='')

    analysis: Mapped['Analysis'] = relationship(back_populates='clauses')
    references: Mapped[List[ClauseReference]] = relationship(
        back_populates='clause', order_by='ClauseReference.position',
        cascade='all, delete-orphan', passive_deletes=True)
    comment: Mapped[Optional[ClauseComment]] = relationship(
        back_populates='clause', cascade='all, delete-orphan', passive_deletes=True, uselist=False)

    def __init__(self, position: int, risk_level: RiskLevel, clause_type: str = 'Unspecified',
                 clause_text: str = '', issue_description: str = '', workaround: str = '',
                 location_page: Optional[int] = None, location_clause_ref: Optional[str] = None,
                 location_raw: Optional[str] = None) -> None:
        self.position = position
        self._risk_level = RiskLevel(risk_level).value
        self.clause_type = clause_type
        self.clause_text = clause_text
        self.issue_description = issue_description
        self.workaround = workaround
        self.location_page = location_page
        self.location_clause_ref = location_clause_ref
        self.location_raw = location_raw

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel(self._risk_level)

    # SQL condition: the clause is at this risk level.
    @classmethod
    def has_risk_level(cls, level: RiskLevel):
        return cls._risk_level == RiskLevel(level).value

    # Best-effort parse of e.g. 'Page 2, Clause 3(f)'; the original text is kept as raw.
    @staticmethod
    def _parse_location(raw) -> dict:
        if raw is None or not str(raw).strip():
            return {'location_page': None, 'location_clause_ref': None, 'location_raw': None}
        text = str(raw).strip()
        page = re.search(r'page\s*(\d+)', text, re.IGNORECASE)
        clause = re.search(r'clause\s*([0-9A-Za-z().]+)', text, re.IGNORECASE)
        return {
            'location_page': int(page.group(1)) if page else None,
            'location_clause_ref': clause.group(1).rstrip('.') if clause else None,
            'location_raw': text,
        }

    # Build a clause from one AI clause object; raises ValueError if it is not valid.
    # Legal references it cites are added to `references` (keyed by id) so the caller can
    # store each source once however many clauses cite it.
    @classmethod
    def from_ai(cls, position: int, raw: dict, references: Dict[str, LegalReference]) -> 'Clause':
        if not isinstance(raw, dict):
            raise ValueError(f"Clause {position} is not an object")
        clause = cls(
            position=position,
            risk_level=RiskLevel.parse(raw.get('risk_level', '')),
            clause_type=str(raw.get('clause_type') or 'Unspecified'),
            clause_text=str(raw.get('clause_text') or ''),
            issue_description=str(raw.get('issue_description') or ''),
            workaround=str(raw.get('workaround') or ''),
            **cls._parse_location(raw.get('line_number')),
        )
        cited = set()
        for item in raw.get('legal_references') or []:
            url = str(item.get('url') or '').strip() if isinstance(item, dict) else ''
            if not url:
                continue
            draft = LegalReference.from_url(url, str(item.get('title') or url))
            reference = references.setdefault(draft.id, draft)
            if reference.id in cited:
                continue
            cited.add(reference.id)
            clause.references.append(ClauseReference(
                position=len(cited), reference_id=reference.id, snippet=str(item.get('summary') or '')))
        return clause
