"""The tables as classes. Each entity checks its own rules: values that have rules (status, role,
risk level, verification) are private and only change through methods such as Analysis.mark_done()."""

import enum
import hashlib
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Type
from urllib.parse import urlparse

from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, Index, Integer, MetaData, Text,
                        UniqueConstraint, func)
from sqlalchemy import Sequence as DbSequence
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

USERNAME_PATTERN = re.compile(r'^[a-z0-9._-]{3,30}$')


class ChoiceEnum(str, enum.Enum):
    """An enumeration stored as its text value."""

    @classmethod
    def _parse(cls, value, field: str):
        try:
            return cls(value)
        except ValueError:
            allowed = ', '.join(member.value for member in cls)
            raise ValueError(f"Invalid {field} '{value}'. Expected one of: {allowed}") from None


class Role(ChoiceEnum):
    CUSTOMER = 'customer'
    ADMIN = 'admin'

    @classmethod
    def parse(cls, value) -> 'Role':
        return cls._parse(value, 'role')


class AnalysisStatus(ChoiceEnum):
    PROCESSING = 'processing'
    DONE = 'done'
    FAILED = 'failed'

    @classmethod
    def parse(cls, value) -> 'AnalysisStatus':
        return cls._parse(value, 'status')


class RiskLevel(ChoiceEnum):
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'

    @classmethod
    def parse(cls, value) -> 'RiskLevel':
        return cls._parse(str(value).strip().upper(), 'risk_level')

    @property
    def label(self) -> str:
        return self.value.lower()

    @property
    def rank(self) -> int:
        return {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}[self.value]


class Verification(ChoiceEnum):
    UNVERIFIED = 'unverified'
    VERIFIED_SG = 'verified_sg'
    REJECTED = 'rejected'

    @classmethod
    def parse(cls, value) -> 'Verification':
        return cls._parse(value, 'verification')


def _in_list(column: str, choices: Type[ChoiceEnum]) -> str:
    """SQL for a CHECK constraint limiting a column to the values of an enumeration."""
    values = ', '.join(f"'{member.value}'" for member in choices)
    return f'{column} IN ({values})'


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _require_text(value, field: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


class Base(DeclarativeBase):
    # Named constraints give readable database error messages (repositories look for 'uq_users_username')
    metadata = MetaData(naming_convention={
        'ix': 'ix_%(column_0_label)s',
        'uq': 'uq_%(table_name)s_%(column_0_name)s',
        'ck': 'ck_%(table_name)s_%(constraint_name)s',
        'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
        'pk': 'pk_%(table_name)s',
    })


# Numbers behind the readable ids (us_001, an_001); formatted by the repositories
USER_ID_SEQUENCE = DbSequence('users_id_seq', metadata=Base.metadata)
ANALYSIS_ID_SEQUENCE = DbSequence('analyses_id_seq', metadata=Base.metadata)


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint(f"username ~ '{USERNAME_PATTERN.pattern}'", name='username_format'),
        CheckConstraint(_in_list('role', Role), name='role'),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    _role: Mapped[str] = mapped_column('role', Text, server_default=Role.CUSTOMER.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __init__(self, id: str, username: str, password_hash: str, role: Role = Role.CUSTOMER) -> None:
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self._role = Role(role).value

    @staticmethod
    def normalise_username(username) -> str:
        return str(username or '').strip().lower()

    @classmethod
    def validated_fields(cls, username, password_hash, role) -> dict:
        """Check and clean the fields for a new user; raises ValueError if any is invalid.
        The password must already be hashed."""
        username = cls.normalise_username(username)
        if not USERNAME_PATTERN.match(username):
            raise ValueError("Username must be 3-30 characters: letters, numbers, '.', '_' or '-'")
        return {
            'username': username,
            'password_hash': _require_text(password_hash, 'password_hash'),
            'role': Role.parse(role),
        }

    @property
    def role(self) -> Role:
        return Role(self._role)

    @property
    def created_at_iso(self) -> str:
        return _iso_utc(self.created_at)

    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


class LegalReference(Base):
    __tablename__ = 'legal_references'
    __table_args__ = (
        CheckConstraint(_in_list('verification', Verification), name='verification'),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    _verification: Mapped[str] = mapped_column('verification', Text,
                                               server_default=Verification.UNVERIFIED.value)

    def __init__(self, id: str, url: str, title: str, domain: str,
                 verification: Verification = Verification.UNVERIFIED) -> None:
        self.id = id
        self.url = url
        self.title = title
        self.domain = domain
        self._verification = Verification(verification).value

    @classmethod
    def from_url(cls, url: str, title: str = '') -> 'LegalReference':
        """A new, unverified reference. The id comes from the URL, so the same source is stored once."""
        host = urlparse(url).netloc.lower()
        return cls(id='ref_' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:12],
                   url=url, title=title or url,
                   domain=host[4:] if host.startswith('www.') else host)

    @property
    def verification(self) -> Verification:
        return Verification(self._verification)

    def verify(self, state: Verification) -> None:
        self._verification = Verification(state).value


class ClauseReference(Base):
    """Links a clause to a legal reference, with the clause-specific snippet.
    Shared references survive when an analysis (and its clauses) is deleted."""
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


class Clause(Base):
    __tablename__ = 'clauses'
    __table_args__ = (
        UniqueConstraint('analysis_id', 'position', name='uq_clauses_analysis_id_position'),
        CheckConstraint(_in_list('risk_level', RiskLevel), name='risk_level'),
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

    @classmethod
    def has_risk_level(cls, level: RiskLevel):
        """SQL condition: the clause is at this risk level."""
        return cls._risk_level == RiskLevel(level).value

    @staticmethod
    def _parse_location(raw) -> dict:
        """Best-effort parse of e.g. 'Page 2, Clause 3(f)'; the original text is kept as raw."""
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

    @classmethod
    def from_ai(cls, position: int, raw: dict, references: Dict[str, LegalReference]) -> 'Clause':
        """Build a clause from one AI clause object; raises ValueError if it is not valid.
        Legal references it cites are added to `references` (keyed by id) so the caller can
        store each source once however many clauses cite it."""
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


class Analysis(Base):
    __tablename__ = 'analyses'
    __table_args__ = (
        CheckConstraint(_in_list('status', AnalysisStatus), name='status'),
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
        return _iso_utc(self.created_at)

    @classmethod
    def has_status(cls, status: AnalysisStatus):
        """SQL condition: the analysis is in this status."""
        return cls._status == AnalysisStatus(status).value

    def mark_done(self, clauses: List[Clause]) -> None:
        self.clauses.extend(clauses)
        self._status = AnalysisStatus.DONE.value

    def mark_failed(self) -> None:
        self._status = AnalysisStatus.FAILED.value

    def discard_clauses(self) -> None:
        self.clauses.clear()

    def counts(self) -> Dict[RiskLevel, int]:
        """Clauses per risk level, worked out from the clauses so it cannot go stale."""
        counts = {level: 0 for level in RiskLevel}
        for clause in self.clauses:
            counts[clause.risk_level] += 1
        return counts

    def is_owned_by(self, user_id: str) -> bool:
        return self.owner_id == user_id
