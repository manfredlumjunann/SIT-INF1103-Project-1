import copy
import hashlib
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence
from urllib.parse import urlparse

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, Index, Integer, MetaData,
                        Text, UniqueConstraint, create_engine, func, select, text)
from sqlalchemy import Sequence as DbSequence
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column, relationship,
                            selectinload, sessionmaker)

logger = logging.getLogger(__name__)

Record = Dict[str, Any]

USER_ROLES = ('customer', 'admin')
USERNAME_PATTERN = re.compile(r'^[a-z0-9._-]{3,30}$')
ANALYSIS_STATUSES = ('processing', 'done', 'failed')
RISK_LEVELS = ('HIGH', 'MEDIUM', 'LOW')
VERIFICATION_STATES = ('unverified', 'verified_sg', 'rejected')


# Database: PostgreSQL connection

class Database:
    """Owns the SQLAlchemy engine for one database and hands out sessions.
    Creating a Database does not connect; the first query does."""

    CONNECT_TIMEOUT_SECONDS = 5

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = self.normalise_url(url)
        self.engine: Engine = create_engine(
            self.url, echo=echo, pool_pre_ping=True,
            connect_args={'connect_timeout': self.CONNECT_TIMEOUT_SECONDS})
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @classmethod
    def from_env(cls, variable: str = 'DATABASE_URL') -> 'Database':
        url = os.getenv(variable)
        if not url:
            raise RuntimeError(f"{variable} is not set")
        return cls(url)

    @staticmethod
    def normalise_url(url: str) -> str:
        """Use the psycopg 3 driver for plain postgres:// or postgresql:// URLs,
        which is the form hosted providers usually give out."""
        url = url.strip()
        for prefix in ('postgres://', 'postgresql://'):
            if url.startswith(prefix):
                return 'postgresql+psycopg://' + url[len(prefix):]
        return url

    @property
    def safe_url(self) -> str:
        return make_url(self.url).render_as_string(hide_password=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text('SELECT 1'))
            return True
        except SQLAlchemyError as e:
            logger.warning("Database %s is not reachable: %s", self.safe_url, e)
            return False

    def schema_revision(self) -> Optional[str]:
        """The Alembic revision the database is at, or None if it was never migrated."""
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()

    def dispose(self) -> None:
        """Close every pooled connection (e.g. at shutdown or between tests)."""
        self.engine.dispose()

    def __repr__(self) -> str:
        return f'Database({self.safe_url!r})'

# Database models: one class per table. to_dict() returns the plain dicts the
# logic layer works with, so it never handles SQLAlchemy objects directly.

def _in_list(column: str, allowed: Sequence[str]) -> str:
    """SQL for a CHECK constraint limiting a column to the allowed values."""
    values = ', '.join(f"'{value}'" for value in allowed)
    return f'{column} IN ({values})'


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class Base(DeclarativeBase):
    # Fixed constraint names so Alembic migrations stay identical on every machine
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
        CheckConstraint(_in_list('role', USER_ROLES), name='role'),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default='customer')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analyses: Mapped[List['Analysis']] = relationship(back_populates='owner')

    def to_dict(self) -> Record:
        return {
            'id': self.id,
            'username': self.username,
            'password_hash': self.password_hash,
            'role': self.role,
            'created_at': _iso_utc(self.created_at),
        }


class LegalReference(Base):
    __tablename__ = 'legal_references'
    __table_args__ = (
        CheckConstraint(_in_list('verification', VERIFICATION_STATES), name='verification'),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(Text, server_default='unverified')

    def to_dict(self) -> Record:
        return {
            'id': self.id,
            'url': self.url,
            'title': self.title,
            'domain': self.domain,
            'verification': self.verification,
        }


class Analysis(Base):
    __tablename__ = 'analyses'
    __table_args__ = (
        CheckConstraint(_in_list('status', ANALYSIS_STATUSES), name='status'),
        # Cache lookup (same owner + same contract and context); also serves owner-only lookups
        Index('ix_analyses_owner_id_contract_hash', 'owner_id', 'contract_hash'),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(Text, server_default='processing')
    filename: Mapped[str] = mapped_column(Text, server_default='')
    context: Mapped[str] = mapped_column(Text, server_default='')
    contract_text: Mapped[str] = mapped_column(Text)
    contract_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped[User] = relationship(back_populates='analyses')
    clauses: Mapped[List['Clause']] = relationship(
        back_populates='analysis', order_by='Clause.position',
        cascade='all, delete-orphan', passive_deletes=True)

    def counts(self) -> Dict[str, int]:
        """Clauses per risk level, worked out from the clauses so it cannot go stale."""
        counts = {level.lower(): 0 for level in RISK_LEVELS}
        for clause in self.clauses:
            counts[clause.risk_level.lower()] += 1
        return counts

    def to_dict(self) -> Record:
        return {
            'id': self.id,
            'owner_id': self.owner_id,
            'status': self.status,
            'filename': self.filename,
            'context': self.context,
            'contract_text': self.contract_text,
            'contract_hash': self.contract_hash,
            'counts': self.counts(),
            'clauses': [clause.to_dict() for clause in self.clauses],
            'created_at': _iso_utc(self.created_at),
        }


class Clause(Base):
    __tablename__ = 'clauses'
    __table_args__ = (
        UniqueConstraint('analysis_id', 'position', name='uq_clauses_analysis_id_position'),
        CheckConstraint(_in_list('risk_level', RISK_LEVELS), name='risk_level'),
        CheckConstraint('position >= 1', name='position_positive'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey('analyses.id', ondelete='CASCADE'))
    position: Mapped[int] = mapped_column(Integer)  # 1-based order; shown as cl_<position>
    clause_type: Mapped[str] = mapped_column(Text, server_default='Unspecified')
    category: Mapped[Optional[Any]] = mapped_column(JSONB)  # passed through from the AI as-is
    risk_level: Mapped[str] = mapped_column(Text)
    clause_text: Mapped[str] = mapped_column(Text, server_default='')
    location_page: Mapped[Optional[int]] = mapped_column(Integer)
    location_clause_ref: Mapped[Optional[str]] = mapped_column(Text)
    location_raw: Mapped[Optional[str]] = mapped_column(Text)
    issue_description: Mapped[str] = mapped_column(Text, server_default='')
    workaround: Mapped[str] = mapped_column(Text, server_default='')

    analysis: Mapped[Analysis] = relationship(back_populates='clauses')
    references: Mapped[List['ClauseReference']] = relationship(
        back_populates='clause', order_by='ClauseReference.position',
        cascade='all, delete-orphan', passive_deletes=True)

    def to_dict(self) -> Record:
        return {
            'id': f'cl_{self.position}',
            'clause_type': self.clause_type,
            'category': self.category,
            'risk_level': self.risk_level,
            'clause_text': self.clause_text,
            'location': {
                'page': self.location_page,
                'clause_ref': self.location_clause_ref,
                'raw': self.location_raw,
            },
            'issue_description': self.issue_description,
            'workaround': self.workaround,
            'references': [link.to_dict() for link in self.references],
        }

    @classmethod
    def from_dict(cls, position: int, data: Record) -> 'Clause':
        """Build a clause (and its reference links) from a stored-schema clause dict,
        i.e. the output of ClauseParser.normalise_clause or Clause.to_dict."""
        location = data.get('location') or {}
        return cls(
            position=position,
            clause_type=data['clause_type'],
            category=data.get('category'),
            risk_level=data['risk_level'],
            clause_text=data['clause_text'],
            location_page=location.get('page'),
            location_clause_ref=location.get('clause_ref'),
            location_raw=location.get('raw'),
            issue_description=data['issue_description'],
            workaround=data['workaround'],
            references=[ClauseReference(position=number, reference_id=link['ref_id'],
                                        snippet=link['snippet'])
                        for number, link in enumerate(data.get('references') or [], start=1)],
        )


class ClauseReference(Base):
    """Links a clause to a legal reference, with the clause-specific snippet.
    Shared references survive when an analysis (and its clauses) is deleted."""
    __tablename__ = 'clause_references'
    __table_args__ = (
        CheckConstraint('position >= 1', name='position_positive'),
    )

    clause_id: Mapped[int] = mapped_column(
        ForeignKey('clauses.id', ondelete='CASCADE'), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference_id: Mapped[str] = mapped_column(ForeignKey('legal_references.id'), index=True)
    snippet: Mapped[str] = mapped_column(Text, server_default='')

    clause: Mapped[Clause] = relationship(back_populates='references')
    reference: Mapped[LegalReference] = relationship()

    def to_dict(self) -> Record:
        return {'ref_id': self.reference_id, 'snippet': self.snippet}

# Shared helpers used by the repositories

def _require_choice(value: str, allowed: Sequence[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"Invalid {field} '{value}'. Expected one of: {', '.join(allowed)}")
    return value


def _require_text(value: Any, field: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _format_id(prefix: str, number: int) -> str:
    return f'{prefix}_{number:03d}'


def _violated_constraint(error: IntegrityError) -> Optional[str]:
    diag = getattr(error.orig, 'diag', None)
    return getattr(diag, 'constraint_name', None)

# Clauses: converting AI output into the stored schema

class ClauseParser:

    @staticmethod
    def parse_location(raw: Any) -> Record:
        """Best-effort parse of e.g. 'Page 2, Clause 3(f)'; the original is kept as raw."""
        if raw is None or not str(raw).strip():
            return {'page': None, 'clause_ref': None, 'raw': None}
        text = str(raw).strip()
        page = re.search(r'page\s*(\d+)', text, re.IGNORECASE)
        clause = re.search(r'clause\s*([0-9A-Za-z().]+)', text, re.IGNORECASE)
        return {
            'page': int(page.group(1)) if page else None,
            'clause_ref': clause.group(1).rstrip('.') if clause else None,
            'raw': text,
        }

    @staticmethod
    def normalise_clause(index: int, raw: Record, references: Dict[str, Record]) -> Record:
        """Convert one AI clause into the stored schema, collecting its references."""
        if not isinstance(raw, dict):
            raise ValueError(f"Clause {index} is not an object")
        risk_level = _require_choice(str(raw.get('risk_level', '')).strip().upper(),
                                     RISK_LEVELS, 'risk_level')

        clause_refs = []
        for ref in raw.get('legal_references') or []:
            url = str(ref.get('url') or '').strip() if isinstance(ref, dict) else ''
            if not url:
                continue
            ref_id = ReferenceRepository.reference_id(url)
            references.setdefault(ref_id, {
                'id': ref_id,
                'url': url,
                'title': str(ref.get('title') or url),
                'domain': ReferenceRepository.domain(url),
                'verification': 'unverified',
            })
            clause_refs.append({'ref_id': ref_id, 'snippet': str(ref.get('summary') or '')})

        return {
            'id': f'cl_{index}',
            'clause_type': str(raw.get('clause_type') or 'Unspecified'),
            'category': raw.get('category'),
            'risk_level': risk_level,
            'clause_text': str(raw.get('clause_text') or ''),
            'location': ClauseParser.parse_location(raw.get('line_number')),
            'issue_description': str(raw.get('issue_description') or ''),
            'workaround': str(raw.get('workaround') or ''),
            'references': clause_refs,
        }

# Users

class UserRepository:

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def normalise_username(username: Any) -> str:
        return str(username or '').strip().lower()

    @staticmethod
    def validate_new_user(username: Any, password_hash: Any, role: str) -> Record:
        """Check and clean the fields for a new user.
        Returns {'username', 'password_hash', 'role'}; raises ValueError if any is invalid."""
        username = UserRepository.normalise_username(username)
        if not USERNAME_PATTERN.match(username):
            raise ValueError("Username must be 3-30 characters: letters, numbers, '.', '_' or '-'")
        _require_choice(role, USER_ROLES, 'role')
        return {
            'username': username,
            'password_hash': _require_text(password_hash, 'password_hash'),
            'role': role,
        }

    def create(self, username: str, password_hash: str, role: str = 'customer') -> Record:
        """Create a user. Usernames are unique regardless of case and stored in lowercase.
        The password must already be hashed by the caller."""
        fields = UserRepository.validate_new_user(username, password_hash, role)
        taken = ValueError(f"Username already taken: {fields['username']}")
        try:
            with self._db.session() as session:
                # Checked first so a taken name does not use up an id number
                if session.scalar(select(User.id).where(User.username == fields['username'])):
                    raise taken
                number = session.scalar(select(USER_ID_SEQUENCE.next_value()))
                user = User(id=_format_id('us', number), **fields)
                session.add(user)
                session.flush()
                session.refresh(user)  # loads created_at set by the database
                return user.to_dict()
        except IntegrityError as e:
            # Two requests registering the same name at once: the unique index decides
            if _violated_constraint(e) == 'uq_users_username':
                raise taken from None
            raise

    def get(self, user_id: str) -> Optional[Record]:
        with self._db.session() as session:
            user = session.get(User, user_id)
            return user.to_dict() if user else None

    def find_by_username(self, username: str) -> Optional[Record]:
        username = UserRepository.normalise_username(username)
        with self._db.session() as session:
            user = session.scalar(select(User).where(User.username == username))
            return user.to_dict() if user else None

    def list(self) -> List[Record]:
        """Every user, ordered by username."""
        with self._db.session() as session:
            return [user.to_dict() for user in session.scalars(select(User).order_by(User.username))]


# References

class ReferenceRepository:
    FIELDS = ('id', 'url', 'title', 'domain', 'verification')

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def reference_id(url: str) -> str:
        """Stable id derived from the URL, so the same source is stored once."""
        return 'ref_' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]

    @staticmethod
    def domain(url: str) -> str:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith('www.') else host

    def get(self, ref_id: str) -> Optional[Record]:
        with self._db.session() as session:
            reference = session.get(LegalReference, ref_id)
            return reference.to_dict() if reference else None

    def add_many(self, references: List[Record]) -> List[Record]:
        """Save references whose id is not stored yet; existing ones are left untouched.
        Returns copies of the references that were added."""
        with self._db.session() as session:
            return self.add_many_in(session, references)

    @classmethod
    def add_many_in(cls, session: Session, references: List[Record]) -> List[Record]:
        """add_many inside an existing transaction (used by AnalysisRepository.complete)."""
        if not references:
            return []
        rows = [{field: reference[field] for field in cls.FIELDS} for reference in references]
        statement = (pg_insert(LegalReference).values(rows)
                     .on_conflict_do_nothing().returning(LegalReference.id))
        added = set(session.scalars(statement))
        return [copy.deepcopy(r) for r in references if r['id'] in added]

    def resolve(self, clause: Record) -> List[Record]:
        """Expand a stored clause's {ref_id, snippet} links into full references for display.
        Links to references that no longer exist are skipped."""
        links = clause.get('references') or []
        if not links:
            return []
        with self._db.session() as session:
            ids = [link.get('ref_id', '') for link in links]
            found = {r.id: r for r in session.scalars(
                select(LegalReference).where(LegalReference.id.in_(ids)))}
            resolved = []
            for link in links:
                ref = found.get(link.get('ref_id', ''))
                if ref is None:
                    continue
                resolved.append({
                    'title': ref.title,
                    'url': ref.url,
                    'summary': link.get('snippet', ''),
                    'domain': ref.domain,
                    'verification': ref.verification,
                })
            return resolved

    def set_verification(self, ref_id: str, verification: str) -> Record:
        _require_choice(verification, VERIFICATION_STATES, 'verification')
        with self._db.session() as session:
            reference = session.get(LegalReference, ref_id)
            if reference is None:
                raise LookupError(f"references record not found: {ref_id}")
            reference.verification = verification
            session.flush()
            return reference.to_dict()


# Analyses

class AnalysisRepository:
    # Load clauses and their reference links up front (2 extra queries, not one per clause)
    WITH_CLAUSES = (selectinload(Analysis.clauses).selectinload(Clause.references),)

    def __init__(self, db: Database, references: ReferenceRepository,
                 parser: ClauseParser) -> None:
        self._db = db
        self._references = references
        self._parser = parser

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

    def create(self, owner_id: str, filename: str, contract_text: str,
               context: str = '') -> Record:
        """Record a new analysis in 'processing' state before the AI runs."""
        with self._db.session() as session:
            if session.get(User, owner_id) is None:
                raise LookupError(f"users record not found: {owner_id}")
            number = session.scalar(select(ANALYSIS_ID_SEQUENCE.next_value()))
            analysis = Analysis(
                id=_format_id('an', number),
                owner_id=owner_id,
                status='processing',
                filename=str(filename or ''),
                context=str(context or ''),
                contract_text=contract_text,
                contract_hash=self.compute_contract_hash(contract_text, context),
                clauses=[],
            )
            session.add(analysis)
            session.flush()
            session.refresh(analysis, ['created_at'])
            return analysis.to_dict()

    def complete(self, analysis_id: str, ai_clauses: List[Record]) -> Record:
        """Store the AI-flagged clauses and mark the analysis done.

        Accepts clauses in the shape returned by the logic layer
        (clause_type, risk_level, clause_text, issue_description, workaround,
        line_number, legal_references[{title, url, summary}]). Every clause is
        validated before anything is written, and the references, clauses and
        status are saved in one transaction.
        """
        with self._db.session() as session:
            analysis = self._require(session, analysis_id)
            new_references: Dict[str, Record] = {}
            clauses = [self._parser.normalise_clause(index, raw, new_references)
                       for index, raw in enumerate(ai_clauses, start=1)]

            self._references.add_many_in(session, list(new_references.values()))

            # Replace any earlier clauses; flush the deletes first so positions can be reused
            analysis.clauses.clear()
            session.flush()
            analysis.clauses.extend(Clause.from_dict(index, clause)
                                    for index, clause in enumerate(clauses, start=1))
            analysis.status = 'done'
            session.flush()
            return analysis.to_dict()

    def fail(self, analysis_id: str) -> Record:
        with self._db.session() as session:
            analysis = self._require(session, analysis_id)
            analysis.status = 'failed'
            session.flush()
            return analysis.to_dict()

    def delete(self, analysis_id: str) -> Record:
        """Permanently remove an analysis. Returns the deleted record.
        Shared legal references are kept because other analyses may cite them."""
        with self._db.session() as session:
            analysis = self._require(session, analysis_id)
            deleted = analysis.to_dict()
            session.delete(analysis)
            return deleted

    # Reading

    def get(self, analysis_id: str) -> Optional[Record]:
        with self._db.session() as session:
            analysis = session.get(Analysis, analysis_id, options=self.WITH_CLAUSES)
            return analysis.to_dict() if analysis else None

    def find_by_hash(self, owner_id: str, contract_hash: str) -> Optional[Record]:
        query = self._newest_first(
            select(Analysis).options(*self.WITH_CLAUSES)
            .where(Analysis.owner_id == owner_id,
                   Analysis.contract_hash == contract_hash,
                   Analysis.status == 'done')).limit(1)
        with self._db.session() as session:
            analysis = session.scalar(query)
            return analysis.to_dict() if analysis else None

    def list(self, owner_id: Optional[str] = None,
             status: Optional[str] = None,
             risk_level: Optional[str] = None) -> List[Record]:
        """Newest first. risk_level keeps only analyses with at least one clause at that level."""
        query = select(Analysis).options(*self.WITH_CLAUSES)
        if status is not None:
            query = query.where(Analysis.status == _require_choice(status, ANALYSIS_STATUSES, 'status'))
        if risk_level is not None:
            level = _require_choice(str(risk_level).strip().upper(), RISK_LEVELS, 'risk_level')
            query = query.where(Analysis.clauses.any(Clause.risk_level == level))
        if owner_id is not None:
            query = query.where(Analysis.owner_id == owner_id)
        with self._db.session() as session:
            return [analysis.to_dict() for analysis in session.scalars(self._newest_first(query))]

    def filter_clauses_by_risk(self, analysis_id: str, risk_level: str) -> List[Record]:
        level = _require_choice(str(risk_level).strip().upper(), RISK_LEVELS, 'risk_level')
        with self._db.session() as session:
            analysis = self._require(session, analysis_id)
            return [clause.to_dict() for clause in analysis.clauses if clause.risk_level == level]


# Facade: builds every repository for one database

ALEMBIC_INI = Path(__file__).with_name('alembic.ini')


def expected_schema_revision() -> Optional[str]:
    """The newest migration shipped with this code, or None if the migration files
    are not available (e.g. data_manager.py copied somewhere on its own)."""
    if not ALEMBIC_INI.exists():
        return None
    return ScriptDirectory.from_config(AlembicConfig(str(ALEMBIC_INI))).get_current_head()


class DataManager:

    def __init__(self, database: Database) -> None:
        self.database = database
        self.users = UserRepository(database)
        self.references = ReferenceRepository(database)
        self.analyses = AnalysisRepository(database, self.references, ClauseParser())

    @classmethod
    def from_env(cls) -> 'DataManager':
        return cls(Database.from_env())

    def init_storage(self) -> Dict[str, int]:
        """Check the database is reachable and fully migrated. Returns record counts."""
        if not self.database.ping():
            raise RuntimeError(f"Cannot connect to the database at {self.database.safe_url}")
        current = self.database.schema_revision()
        expected = expected_schema_revision()
        if current is None or (expected is not None and current != expected):
            raise RuntimeError(
                f"Database schema is at revision {current or '(none)'} but this code expects "
                f"{expected or 'a migrated schema'}. Run: alembic upgrade head")
        counts = self.counts()
        logger.info("Connected to %s (schema %s): %s", self.database.safe_url, current, counts)
        return counts

    def counts(self) -> Dict[str, int]:
        tables = (('users', User), ('analyses', Analysis), ('references', LegalReference))
        with self.database.session() as session:
            return {name: session.scalar(select(func.count()).select_from(model))
                    for name, model in tables}

    def close(self) -> None:
        self.database.dispose()

# Public API: module-level functions used by the logic layer. Each one forwards to
# the default DataManager, which connects using the DATABASE_URL environment variable.

_default: Optional[DataManager] = None
_default_lock = threading.Lock()


def init_storage() -> Dict[str, int]:
    """Connect using DATABASE_URL, check the schema is migrated, and return record counts.
    DATABASE_URL is read now rather than at import, so a .env loaded after importing
    this module is still used."""
    global _default
    manager = DataManager.from_env()
    try:
        counts = manager.init_storage()
    except Exception:
        manager.close()
        raise
    with _default_lock:
        previous, _default = _default, manager
    if previous is not None:
        previous.close()
    return counts


def default_manager() -> DataManager:
    """The DataManager the functions below use (created from DATABASE_URL on first use)."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = DataManager.from_env()
    return _default

# Users

def create_user(username: str, password_hash: str, role: str = 'customer') -> Record:
    return default_manager().users.create(username, password_hash, role)


def get_user(user_id: str) -> Optional[Record]:
    return default_manager().users.get(user_id)


def find_user_by_username(username: str) -> Optional[Record]:
    return default_manager().users.find_by_username(username)


def list_users() -> List[Record]:
    """Every user, ordered by username."""
    return default_manager().users.list()

# References

def get_reference(ref_id: str) -> Optional[Record]:
    return default_manager().references.get(ref_id)


def resolve_references(clause: Record) -> List[Record]:
    """Expand a stored clause's {ref_id, snippet} links into full references for display.
    Links to references that no longer exist are skipped."""
    return default_manager().references.resolve(clause)


def set_reference_verification(ref_id: str, verification: str) -> Record:
    return default_manager().references.set_verification(ref_id, verification)

# Analyses

def compute_contract_hash(contract_text: str, context: str = '') -> str:
    """Identical contract text AND context produce the same hash, so a repeat
    request can reuse a saved result while a new question forces a rescan."""
    return AnalysisRepository.compute_contract_hash(contract_text, context)


def create_analysis(owner_id: str, filename: str, contract_text: str,
                    context: str = '') -> Record:
    """Record a new analysis in 'processing' state before the AI runs."""
    return default_manager().analyses.create(owner_id, filename, contract_text, context)


def complete_analysis(analysis_id: str, ai_clauses: List[Record]) -> Record:
    """Store the AI-flagged clauses and mark the analysis done."""
    return default_manager().analyses.complete(analysis_id, ai_clauses)


def fail_analysis(analysis_id: str) -> Record:
    return default_manager().analyses.fail(analysis_id)


def delete_analysis(analysis_id: str) -> Record:
    """Permanently remove an analysis. Returns the deleted record."""
    return default_manager().analyses.delete(analysis_id)


def get_analysis(analysis_id: str) -> Optional[Record]:
    return default_manager().analyses.get(analysis_id)


def find_analysis_by_hash(owner_id: str, contract_hash: str) -> Optional[Record]:
    return default_manager().analyses.find_by_hash(owner_id, contract_hash)


def list_analyses(owner_id: Optional[str] = None,
                  status: Optional[str] = None,
                  risk_level: Optional[str] = None) -> List[Record]:
    """Newest first. risk_level keeps only analyses with at least one clause at that level."""
    return default_manager().analyses.list(owner_id, status, risk_level)


def filter_clauses_by_risk(analysis_id: str, risk_level: str) -> List[Record]:
    return default_manager().analyses.filter_clauses_by_risk(analysis_id, risk_level)
