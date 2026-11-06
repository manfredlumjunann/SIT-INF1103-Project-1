import copy
import hashlib
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

Record = Dict[str, Any]

DATA_DIR = Path(os.getenv('DATA_DIR', str(Path(__file__).with_name('data'))))

COLLECTIONS = ('users', 'analyses', 'references')
ID_PREFIXES = {'users': 'us', 'analyses': 'an'}

USER_ROLES = ('customer', 'admin')
USERNAME_PATTERN = re.compile(r'^[a-z0-9._-]{3,30}$')
ANALYSIS_STATUSES = ('processing', 'done', 'failed')
RISK_LEVELS = ('HIGH', 'MEDIUM', 'LOW')
VERIFICATION_STATES = ('unverified', 'verified_sg', 'rejected')

# Fields every stored record must have (and their types). Records that do not match are
# skipped on load so one damaged entry cannot crash lookups for everyone else.
REQUIRED_FIELDS: Dict[str, Dict[str, type]] = {
    'users': {'id': str, 'username': str, 'password_hash': str, 'role': str, 'created_at': str},
    'analyses': {'id': str, 'owner_id': str, 'status': str, 'filename': str, 'context': str,
                 'contract_text': str, 'contract_hash': str, 'counts': dict, 'clauses': list,
                 'created_at': str},
    'references': {'id': str, 'url': str, 'title': str, 'domain': str, 'verification': str},
}
# Clause fields the app reads when displaying an analysis
REQUIRED_CLAUSE_FIELDS: Dict[str, type] = {'id': str, 'risk_level': str, 'location': dict, 'references': list}

# Storage: loading, validation and atomic writes

class JsonStore:

    def __init__(self, data_dir: Union[str, Path]) -> None:
        self.data_dir = Path(data_dir)
        self._records: Dict[str, List[Record]] = {name: [] for name in COLLECTIONS}
        self._loaded = False
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    # Loading

    def load(self, data_dir: Optional[Union[str, Path]] = None) -> Dict[str, int]:
        """Load every collection from disk into memory. Returns record counts."""
        with self._lock:
            if data_dir is not None:
                self.data_dir = Path(data_dir)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            for name in COLLECTIONS:
                self._records[name] = self._load_collection(name)
            self._loaded = True
            counts = {name: len(self._records[name]) for name in COLLECTIONS}
        logger.info("Loaded records from %s: %s", self.data_dir, counts)
        return counts

    def ensure_loaded(self) -> None:
        with self._lock:
            if not self._loaded:
                self.load()

    def _load_collection(self, collection: str) -> List[Record]:
        """Read one collection file. A missing file starts empty. A file that is not a JSON
        list is moved aside and starts empty. Individual invalid records are skipped."""
        path = self._file_path(collection)
        if not path.exists():
            return []
        try:
            with path.open(encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("expected a JSON list of records")
        except (OSError, ValueError) as e:
            quarantine = path.with_name(f'{path.name}.corrupt-{self._file_timestamp()}')
            logger.warning("Corrupt data file %s (%s); moved to %s", path, e, quarantine.name)
            try:
                path.replace(quarantine)
            except OSError as move_error:
                logger.error("Could not move corrupt file %s: %s", path, move_error)
            return []

        valid: List[Record] = []
        seen_ids = set()
        problems = []
        for position, record in enumerate(data):
            problem = self._record_problem(collection, record)
            if problem is None and record['id'] in seen_ids:
                problem = 'duplicate id'
            if problem is not None:
                label = record.get('id') if isinstance(record, dict) else None
                problems.append(f"item {position} ({label if label is not None else 'no id'}): {problem}")
                continue
            seen_ids.add(record['id'])
            valid.append(record)

        if problems:
            self._set_aside_invalid_records(collection, path, valid, problems)
        return valid

    def _set_aside_invalid_records(self, collection: str, path: Path, valid: List[Record],
                                   problems: List[str]) -> None:
        """Keep a copy of the original file, then rewrite it with only the valid records
        so the same problems are not reported again on the next start."""
        backup = path.with_name(f'{path.name}.invalid-{self._file_timestamp()}')
        logger.warning("Skipped %d invalid record(s) in %s (original kept as %s): %s",
                       len(problems), path.name, backup.name, '; '.join(problems))
        try:
            shutil.copy2(path, backup)
            self.write(collection, valid)
        except OSError as e:
            # The invalid records are still skipped in memory; the file is left as it was
            logger.error("Could not back up or clean %s: %s", path, e)

    def _file_path(self, collection: str) -> Path:
        return self.data_dir / f'{collection}.json'

    @staticmethod
    def _file_timestamp() -> str:
        return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')

    # Validation

    @staticmethod
    def _record_problem(collection: str, record: Any) -> Optional[str]:
        if not isinstance(record, dict):
            return 'not an object'
        problem = JsonStore._missing_or_wrong_field(record, REQUIRED_FIELDS[collection])
        if problem is not None:
            return problem
        if collection == 'analyses':
            for number, clause in enumerate(record['clauses'], start=1):
                if not isinstance(clause, dict):
                    return f'clause {number} is not an object'
                problem = JsonStore._missing_or_wrong_field(clause, REQUIRED_CLAUSE_FIELDS)
                if problem is not None:
                    return f'clause {number}: {problem}'
        return None

    @staticmethod
    def _missing_or_wrong_field(record: Record, fields: Dict[str, type]) -> Optional[str]:
        for field, expected_type in fields.items():
            if field not in record:
                return f"missing '{field}'"
            if not isinstance(record[field], expected_type):
                return f"'{field}' should be {expected_type.__name__}"
        return None

    # Writing

    def write(self, collection: str, records: List[Record]) -> None:
        """Atomically replace a collection file, then the in-memory copy."""
        with self._lock:
            path = self._file_path(collection)
            tmp_path = path.with_name(f'{path.name}.tmp')
            with tmp_path.open('w', encoding='utf-8') as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            self._records[collection] = records

    def append(self, collection: str, record: Record) -> Record:
        with self._lock:
            self.write(collection, self._records[collection] + [record])
            return copy.deepcopy(record)

    def update(self, collection: str, record_id: str, changes: Record) -> Record:
        with self._lock:
            records = list(self._records[collection])
            for index, record in enumerate(records):
                if record.get('id') == record_id:
                    updated = {**copy.deepcopy(record), **changes}
                    records[index] = updated
                    self.write(collection, records)
                    return copy.deepcopy(updated)
        raise LookupError(f"{collection} record not found: {record_id}")

    # Reading

    def records(self, collection: str) -> List[Record]:
        """The live list for a collection. Callers must copy records before handing them out."""
        return self._records[collection]

    def find(self, collection: str, record_id: str) -> Optional[Record]:
        with self._lock:
            for record in self._records[collection]:
                if record.get('id') == record_id:
                    return record
        return None

    def require(self, collection: str, record_id: str) -> Record:
        record = self.find(collection, record_id)
        if record is None:
            raise LookupError(f"{collection} record not found: {record_id}")
        return record

    def next_id(self, collection: str) -> str:
        prefix = ID_PREFIXES[collection]
        pattern = re.compile(rf'^{prefix}_(\d+)$')
        with self._lock:
            numbers = [int(m.group(1)) for r in self._records[collection]
                       if (m := pattern.match(str(r.get('id', ''))))]
        return f'{prefix}_{max(numbers, default=0) + 1:03d}'

# Shared helpers used by the user, analysis and reference functions

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_choice(value: str, allowed: Sequence[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"Invalid {field} '{value}'. Expected one of: {', '.join(allowed)}")
    return value


def _require_text(value: Any, field: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _newest_first(records: List[Record]) -> List[Record]:
    ordered = sorted(records, key=lambda r: (r.get('created_at', ''), r.get('id', '')), reverse=True)
    return [copy.deepcopy(r) for r in ordered]

# Users

class UserRepository:
    COLLECTION = 'users'

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    @staticmethod
    def normalise_username(username: Any) -> str:
        return str(username or '').strip().lower()

    def create(self, username: str, password_hash: str, role: str = 'customer') -> Record:
        """Create a user. Usernames are unique regardless of case and stored in lowercase.
        The password must already be hashed by the caller."""
        username = self.normalise_username(username)
        if not USERNAME_PATTERN.match(username):
            raise ValueError("Username must be 3-30 characters: letters, numbers, '.', '_' or '-'")
        _require_choice(role, USER_ROLES, 'role')
        record = {
            'id': '',
            'username': username,
            'password_hash': _require_text(password_hash, 'password_hash'),
            'role': role,
            'created_at': _now(),
        }
        with self._store.lock:
            self._store.ensure_loaded()
            if any(u['username'] == username for u in self._store.records(self.COLLECTION)):
                raise ValueError(f"Username already taken: {username}")
            record['id'] = self._store.next_id(self.COLLECTION)
            return self._store.append(self.COLLECTION, record)

    def get(self, user_id: str) -> Optional[Record]:
        with self._store.lock:
            self._store.ensure_loaded()
            return copy.deepcopy(self._store.find(self.COLLECTION, user_id))

    def find_by_username(self, username: str) -> Optional[Record]:
        username = self.normalise_username(username)
        with self._store.lock:
            self._store.ensure_loaded()
            for user in self._store.records(self.COLLECTION):
                if user['username'] == username:
                    return copy.deepcopy(user)
        return None

    def list(self) -> List[Record]:
        """Every user, ordered by username."""
        with self._store.lock:
            self._store.ensure_loaded()
            users = sorted(self._store.records(self.COLLECTION), key=lambda u: u['username'])
            return [copy.deepcopy(u) for u in users]

# References

class ReferenceRepository:
    COLLECTION = 'references'

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    @staticmethod
    def reference_id(url: str) -> str:
        """Stable id derived from the URL, so the same source is stored once."""
        return 'ref_' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]

    @staticmethod
    def domain(url: str) -> str:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith('www.') else host

    def get(self, ref_id: str) -> Optional[Record]:
        with self._store.lock:
            self._store.ensure_loaded()
            return copy.deepcopy(self._store.find(self.COLLECTION, ref_id))

    def add_many(self, references: List[Record]) -> List[Record]:
        """Save references whose id is not stored yet; existing ones are left untouched.
        Returns copies of the references that were added."""
        with self._store.lock:
            self._store.ensure_loaded()
            existing = self._store.records(self.COLLECTION)
            known_ids = {r['id'] for r in existing}
            additions = [r for r in references if r['id'] not in known_ids]
            if additions:
                self._store.write(self.COLLECTION, existing + additions)
            return [copy.deepcopy(r) for r in additions]

    def resolve(self, clause: Record) -> List[Record]:
        """Expand a stored clause's {ref_id, snippet} links into full references for display.
        Links to references that no longer exist are skipped."""
        resolved = []
        with self._store.lock:
            self._store.ensure_loaded()
            for link in clause.get('references') or []:
                ref = self._store.find(self.COLLECTION, link.get('ref_id', ''))
                if ref is None:
                    continue
                resolved.append({
                    'title': ref['title'],
                    'url': ref['url'],
                    'summary': link.get('snippet', ''),
                    'domain': ref['domain'],
                    'verification': ref['verification'],
                })
        return resolved

    def set_verification(self, ref_id: str, verification: str) -> Record:
        _require_choice(verification, VERIFICATION_STATES, 'verification')
        with self._store.lock:
            self._store.ensure_loaded()
            return self._store.update(self.COLLECTION, ref_id, {'verification': verification})

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

# Analyses

class AnalysisRepository:
    COLLECTION = 'analyses'

    def __init__(self, store: JsonStore, users: UserRepository,
                 references: ReferenceRepository, parser: ClauseParser) -> None:
        self._store = store
        self._users = users
        self._references = references
        self._parser = parser

    @staticmethod
    def compute_contract_hash(contract_text: str, context: str = '') -> str:
        """Identical contract text AND context produce the same hash, so a repeat
        request can reuse a saved result while a new question forces a rescan."""
        payload = json.dumps([contract_text, context], ensure_ascii=False)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    # Writing

    def create(self, owner_id: str, filename: str, contract_text: str,
               context: str = '') -> Record:
        """Record a new analysis in 'processing' state before the AI runs."""
        with self._store.lock:
            self._store.ensure_loaded()
            if self._users.get(owner_id) is None:
                raise LookupError(f"users record not found: {owner_id}")
            record = {
                'id': self._store.next_id(self.COLLECTION),
                'owner_id': owner_id,
                'status': 'processing',
                'filename': str(filename or ''),
                'context': str(context or ''),
                'contract_text': contract_text,
                'contract_hash': self.compute_contract_hash(contract_text, context),
                'counts': {'high': 0, 'medium': 0, 'low': 0},
                'clauses': [],
                'created_at': _now(),
            }
            return self._store.append(self.COLLECTION, record)

    def complete(self, analysis_id: str, ai_clauses: List[Record]) -> Record:
        """Store the AI-flagged clauses and mark the analysis done.

        Accepts clauses in the shape returned by the logic layer
        (clause_type, risk_level, clause_text, issue_description, workaround,
        line_number, legal_references[{title, url, summary}]). Every clause is
        validated before anything is written.
        """
        with self._store.lock:
            self._store.ensure_loaded()
            self._store.require(self.COLLECTION, analysis_id)
            new_references: Dict[str, Record] = {}
            clauses = [self._parser.normalise_clause(index, raw, new_references)
                       for index, raw in enumerate(ai_clauses, start=1)]

            self._references.add_many(list(new_references.values()))

            counts = {level.lower(): 0 for level in RISK_LEVELS}
            for clause in clauses:
                counts[clause['risk_level'].lower()] += 1

            return self._store.update(self.COLLECTION, analysis_id,
                                      {'status': 'done', 'clauses': clauses, 'counts': counts})

    def fail(self, analysis_id: str) -> Record:
        with self._store.lock:
            self._store.ensure_loaded()
            return self._store.update(self.COLLECTION, analysis_id, {'status': 'failed'})

    def delete(self, analysis_id: str) -> Record:
        """Permanently remove an analysis. Returns the deleted record.
        Shared legal references are kept because other analyses may cite them."""
        with self._store.lock:
            self._store.ensure_loaded()
            analysis = self._store.require(self.COLLECTION, analysis_id)
            remaining = [a for a in self._store.records(self.COLLECTION) if a['id'] != analysis_id]
            self._store.write(self.COLLECTION, remaining)
            return copy.deepcopy(analysis)

    # Reading

    def get(self, analysis_id: str) -> Optional[Record]:
        with self._store.lock:
            self._store.ensure_loaded()
            return copy.deepcopy(self._store.find(self.COLLECTION, analysis_id))

    def find_by_hash(self, owner_id: str, contract_hash: str) -> Optional[Record]:
        with self._store.lock:
            self._store.ensure_loaded()
            matches = [a for a in self._store.records(self.COLLECTION)
                       if a['owner_id'] == owner_id
                       and a['contract_hash'] == contract_hash
                       and a['status'] == 'done']
            return _newest_first(matches)[0] if matches else None

    def list(self, owner_id: Optional[str] = None,
             status: Optional[str] = None,
             risk_level: Optional[str] = None) -> List[Record]:
        """Newest first. risk_level keeps only analyses with at least one clause at that level."""
        if status is not None:
            _require_choice(status, ANALYSIS_STATUSES, 'status')
        level = None
        if risk_level is not None:
            level = _require_choice(str(risk_level).strip().upper(), RISK_LEVELS, 'risk_level')
        with self._store.lock:
            self._store.ensure_loaded()
            matches = [a for a in self._store.records(self.COLLECTION)
                       if (owner_id is None or a['owner_id'] == owner_id)
                       and (status is None or a['status'] == status)
                       and (level is None or a['counts'].get(level.lower(), 0) > 0)]
            return _newest_first(matches)

    def filter_clauses_by_risk(self, analysis_id: str, risk_level: str) -> List[Record]:
        level = _require_choice(str(risk_level).strip().upper(), RISK_LEVELS, 'risk_level')
        with self._store.lock:
            self._store.ensure_loaded()
            analysis = self._store.require(self.COLLECTION, analysis_id)
            return [copy.deepcopy(c) for c in analysis['clauses'] if c['risk_level'] == level]



class DataManager:

    def __init__(self, data_dir: Union[str, Path]) -> None:
        self.store = JsonStore(data_dir)
        self.users = UserRepository(self.store)
        self.references = ReferenceRepository(self.store)
        self.analyses = AnalysisRepository(self.store, self.users, self.references, ClauseParser())

    @property
    def data_dir(self) -> Path:
        return self.store.data_dir

    def init_storage(self) -> Dict[str, int]:
        return self.store.load()

_default = DataManager(DATA_DIR)


def init_storage(data_dir: Optional[Union[str, Path]] = None) -> Dict[str, int]:
    global _default
    if data_dir is not None:
        _default = DataManager(data_dir)
    return _default.init_storage()

# Users

def create_user(username: str, password_hash: str, role: str = 'customer') -> Record:
    return _default.users.create(username, password_hash, role)


def get_user(user_id: str) -> Optional[Record]:
    return _default.users.get(user_id)


def find_user_by_username(username: str) -> Optional[Record]:
    return _default.users.find_by_username(username)


def list_users() -> List[Record]:
    """Every user, ordered by username."""
    return _default.users.list()

# References

def get_reference(ref_id: str) -> Optional[Record]:
    return _default.references.get(ref_id)


def resolve_references(clause: Record) -> List[Record]:
    """Expand a stored clause's {ref_id, snippet} links into full references for display.
    Links to references that no longer exist are skipped."""
    return _default.references.resolve(clause)


def set_reference_verification(ref_id: str, verification: str) -> Record:
    return _default.references.set_verification(ref_id, verification)

# Analyses

def compute_contract_hash(contract_text: str, context: str = '') -> str:
    """Identical contract text AND context produce the same hash, so a repeat
    request can reuse a saved result while a new question forces a rescan."""
    return AnalysisRepository.compute_contract_hash(contract_text, context)


def create_analysis(owner_id: str, filename: str, contract_text: str,
                    context: str = '') -> Record:
    """Record a new analysis in 'processing' state before the AI runs."""
    return _default.analyses.create(owner_id, filename, contract_text, context)


def complete_analysis(analysis_id: str, ai_clauses: List[Record]) -> Record:
    """Store the AI-flagged clauses and mark the analysis done."""
    return _default.analyses.complete(analysis_id, ai_clauses)


def fail_analysis(analysis_id: str) -> Record:
    return _default.analyses.fail(analysis_id)


def delete_analysis(analysis_id: str) -> Record:
    """Permanently remove an analysis. Returns the deleted record."""
    return _default.analyses.delete(analysis_id)


def get_analysis(analysis_id: str) -> Optional[Record]:
    return _default.analyses.get(analysis_id)


def find_analysis_by_hash(owner_id: str, contract_hash: str) -> Optional[Record]:
    return _default.analyses.find_by_hash(owner_id, contract_hash)


def list_analyses(owner_id: Optional[str] = None,
                  status: Optional[str] = None,
                  risk_level: Optional[str] = None) -> List[Record]:
    """Newest first. risk_level keeps only analyses with at least one clause at that level."""
    return _default.analyses.list(owner_id, status, risk_level)


def filter_clauses_by_risk(analysis_id: str, risk_level: str) -> List[Record]:
    return _default.analyses.filter_clauses_by_risk(analysis_id, risk_level)
