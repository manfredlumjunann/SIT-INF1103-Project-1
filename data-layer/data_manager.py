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

COLLECTIONS = ('users', 'analyses', 'references', 'tickets')
ID_PREFIXES = {'users': 'us', 'analyses': 'an', 'tickets': 'tk'}

USER_ROLES = ('customer', 'admin')
USERNAME_PATTERN = re.compile(r'^[a-z0-9._-]{3,30}$')
ANALYSIS_STATUSES = ('processing', 'done', 'failed')
RISK_LEVELS = ('HIGH', 'MEDIUM', 'LOW')
TICKET_STATUSES = ('open', 'in_progress', 'resolved')
VERIFICATION_STATES = ('unverified', 'verified_sg', 'rejected')

# Fields every stored record must have (and their types). Records that do not match are
# skipped on load so one damaged entry cannot crash lookups for everyone else.
REQUIRED_FIELDS: Dict[str, Dict[str, type]] = {
    'users': {'id': str, 'username': str, 'password_hash': str, 'role': str, 'created_at': str},
    'analyses': {'id': str, 'owner_id': str, 'status': str, 'filename': str, 'context': str,
                 'contract_text': str, 'contract_hash': str, 'counts': dict, 'clauses': list,
                 'created_at': str},
    'references': {'id': str, 'url': str, 'title': str, 'domain': str, 'verification': str},
    'tickets': {'id': str, 'owner_id': str, 'analysis_id': str, 'subject': str, 'status': str,
                'messages': list, 'created_at': str},
}
# Clause fields the app reads when displaying an analysis
REQUIRED_CLAUSE_FIELDS: Dict[str, type] = {'id': str, 'risk_level': str, 'location': dict, 'references': list}

_store: Dict[str, List[Record]] = {name: [] for name in COLLECTIONS}
_loaded = False
_lock = threading.RLock()

def init_storage(data_dir: Optional[Union[str, Path]] = None) -> Dict[str, int]:
    """Load every collection from disk into memory. Returns record counts."""
    global DATA_DIR, _loaded
    with _lock:
        if data_dir is not None:
            DATA_DIR = Path(data_dir)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for name in COLLECTIONS:
            _store[name] = _load_collection(name)
        _loaded = True
        counts = {name: len(_store[name]) for name in COLLECTIONS}
    logger.info("Loaded records from %s: %s", DATA_DIR, counts)
    return counts


def _ensure_loaded() -> None:
    if not _loaded:
        init_storage()


def _file_path(collection: str) -> Path:
    return DATA_DIR / f'{collection}.json'


def _load_collection(collection: str) -> List[Record]:
    """Read one collection file. A missing file starts empty. A file that is not a JSON
    list is moved aside and starts empty. Individual invalid records are skipped."""
    path = _file_path(collection)
    if not path.exists():
        return []
    try:
        with path.open(encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("expected a JSON list of records")
    except (OSError, ValueError) as e:
        quarantine = path.with_name(f'{path.name}.corrupt-{_file_timestamp()}')
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
        problem = _record_problem(collection, record)
        if problem is None and record['id'] in seen_ids:
            problem = 'duplicate id'
        if problem is not None:
            label = record.get('id') if isinstance(record, dict) else None
            problems.append(f"item {position} ({label if label is not None else 'no id'}): {problem}")
            continue
        seen_ids.add(record['id'])
        valid.append(record)

    if problems:
        _set_aside_invalid_records(collection, path, valid, problems)
    return valid


def _record_problem(collection: str, record: Any) -> Optional[str]:
    """Why a stored record is unusable, or None if it has every required field."""
    if not isinstance(record, dict):
        return 'not an object'
    problem = _missing_or_wrong_field(record, REQUIRED_FIELDS[collection])
    if problem is not None:
        return problem
    if collection == 'analyses':
        for number, clause in enumerate(record['clauses'], start=1):
            if not isinstance(clause, dict):
                return f'clause {number} is not an object'
            problem = _missing_or_wrong_field(clause, REQUIRED_CLAUSE_FIELDS)
            if problem is not None:
                return f'clause {number}: {problem}'
    return None


def _missing_or_wrong_field(record: Record, fields: Dict[str, type]) -> Optional[str]:
    for field, expected_type in fields.items():
        if field not in record:
            return f"missing '{field}'"
        if not isinstance(record[field], expected_type):
            return f"'{field}' should be {expected_type.__name__}"
    return None


def _set_aside_invalid_records(collection: str, path: Path, valid: List[Record],
                               problems: List[str]) -> None:
    """Keep a copy of the original file, then rewrite it with only the valid records
    so the same problems are not reported again on the next start."""
    backup = path.with_name(f'{path.name}.invalid-{_file_timestamp()}')
    logger.warning("Skipped %d invalid record(s) in %s (original kept as %s): %s",
                   len(problems), path.name, backup.name, '; '.join(problems))
    try:
        shutil.copy2(path, backup)
        _write_collection(collection, valid)
    except OSError as e:
        # The invalid records are still skipped in memory; the file is left as it was
        logger.error("Could not back up or clean %s: %s", path, e)


def _file_timestamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def _write_collection(collection: str, records: List[Record]) -> None:
    path = _file_path(collection)
    tmp_path = path.with_name(f'{path.name}.tmp')
    with tmp_path.open('w', encoding='utf-8') as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    _store[collection] = records


def _append(collection: str, record: Record) -> Record:
    _write_collection(collection, _store[collection] + [record])
    return copy.deepcopy(record)


def _update(collection: str, record_id: str, changes: Record) -> Record:
    records = list(_store[collection])
    for index, record in enumerate(records):
        if record.get('id') == record_id:
            updated = {**copy.deepcopy(record), **changes}
            records[index] = updated
            _write_collection(collection, records)
            return copy.deepcopy(updated)
    raise LookupError(f"{collection} record not found: {record_id}")


def _find(collection: str, record_id: str) -> Optional[Record]:
    for record in _store[collection]:
        if record.get('id') == record_id:
            return record
    return None


def _require(collection: str, record_id: str) -> Record:
    record = _find(collection, record_id)
    if record is None:
        raise LookupError(f"{collection} record not found: {record_id}")
    return record


def _next_id(collection: str) -> str:
    prefix = ID_PREFIXES[collection]
    pattern = re.compile(rf'^{prefix}_(\d+)$')
    numbers = [int(m.group(1)) for r in _store[collection]
               if (m := pattern.match(str(r.get('id', ''))))]
    return f'{prefix}_{max(numbers, default=0) + 1:03d}'


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

def create_user(username: str, password_hash: str, role: str = 'customer') -> Record:
    """Create a user. Usernames are unique regardless of case and stored in lowercase.
    The password must already be hashed by the caller."""
    username = _normalise_username(username)
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
    with _lock:
        _ensure_loaded()
        if any(u['username'] == username for u in _store['users']):
            raise ValueError(f"Username already taken: {username}")
        record['id'] = _next_id('users')
        return _append('users', record)


def _normalise_username(username: Any) -> str:
    return str(username or '').strip().lower()


def get_user(user_id: str) -> Optional[Record]:
    with _lock:
        _ensure_loaded()
        return copy.deepcopy(_find('users', user_id))


def find_user_by_username(username: str) -> Optional[Record]:
    username = _normalise_username(username)
    with _lock:
        _ensure_loaded()
        for user in _store['users']:
            if user['username'] == username:
                return copy.deepcopy(user)
    return None


# analysis and clauses

def compute_contract_hash(contract_text: str, context: str = '') -> str:
    """Identical contract text AND context produce the same hash, so a repeat
    request can reuse a saved result while a new question forces a rescan."""
    payload = json.dumps([contract_text, context], ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def create_analysis(owner_id: str, filename: str, contract_text: str,
                    context: str = '') -> Record:
    """Record a new analysis in 'processing' state before the AI runs."""
    with _lock:
        _ensure_loaded()
        _require('users', owner_id)
        record = {
            'id': _next_id('analyses'),
            'owner_id': owner_id,
            'status': 'processing',
            'filename': str(filename or ''),
            'context': str(context or ''),
            'contract_text': contract_text,
            'contract_hash': compute_contract_hash(contract_text, context),
            'counts': {'high': 0, 'medium': 0, 'low': 0},
            'clauses': [],
            'created_at': _now(),
        }
        return _append('analyses', record)


def complete_analysis(analysis_id: str, ai_clauses: List[Record]) -> Record:
    """Store the AI-flagged clauses and mark the analysis done.

    Accepts clauses in the shape returned by the logic layer
    (clause_type, risk_level, clause_text, issue_description, workaround,
    line_number, legal_references[{title, url, summary}]). Every clause is
    validated before anything is written.
    """
    with _lock:
        _ensure_loaded()
        _require('analyses', analysis_id)
        new_references: Dict[str, Record] = {}
        clauses = [_normalise_clause(index, raw, new_references)
                   for index, raw in enumerate(ai_clauses, start=1)]

        known_ids = {r['id'] for r in _store['references']}
        additions = [r for ref_id, r in new_references.items() if ref_id not in known_ids]
        if additions:
            _write_collection('references', _store['references'] + additions)

        counts = {level.lower(): 0 for level in RISK_LEVELS}
        for clause in clauses:
            counts[clause['risk_level'].lower()] += 1

        return _update('analyses', analysis_id,
                       {'status': 'done', 'clauses': clauses, 'counts': counts})


def fail_analysis(analysis_id: str) -> Record:
    with _lock:
        _ensure_loaded()
        return _update('analyses', analysis_id, {'status': 'failed'})


def get_analysis(analysis_id: str) -> Optional[Record]:
    with _lock:
        _ensure_loaded()
        return copy.deepcopy(_find('analyses', analysis_id))


def find_analysis_by_hash(owner_id: str, contract_hash: str) -> Optional[Record]:
    with _lock:
        _ensure_loaded()
        matches = [a for a in _store['analyses']
                   if a['owner_id'] == owner_id
                   and a['contract_hash'] == contract_hash
                   and a['status'] == 'done']
        return _newest_first(matches)[0] if matches else None


def list_analyses(owner_id: Optional[str] = None,
                  status: Optional[str] = None,
                  risk_level: Optional[str] = None) -> List[Record]:
    """Newest first. risk_level keeps only analyses with at least one clause at that level."""
    if status is not None:
        _require_choice(status, ANALYSIS_STATUSES, 'status')
    level = None
    if risk_level is not None:
        level = _require_choice(str(risk_level).strip().upper(), RISK_LEVELS, 'risk_level')
    with _lock:
        _ensure_loaded()
        matches = [a for a in _store['analyses']
                   if (owner_id is None or a['owner_id'] == owner_id)
                   and (status is None or a['status'] == status)
                   and (level is None or a['counts'].get(level.lower(), 0) > 0)]
        return _newest_first(matches)


def filter_clauses_by_risk(analysis_id: str, risk_level: str) -> List[Record]:
    level = _require_choice(str(risk_level).strip().upper(), RISK_LEVELS, 'risk_level')
    with _lock:
        _ensure_loaded()
        analysis = _require('analyses', analysis_id)
        return [copy.deepcopy(c) for c in analysis['clauses'] if c['risk_level'] == level]


def _normalise_clause(index: int, raw: Record, references: Dict[str, Record]) -> Record:
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
        ref_id = _reference_id(url)
        references.setdefault(ref_id, {
            'id': ref_id,
            'url': url,
            'title': str(ref.get('title') or url),
            'domain': _domain(url),
            'verification': 'unverified',
        })
        clause_refs.append({'ref_id': ref_id, 'snippet': str(ref.get('summary') or '')})

    return {
        'id': f'cl_{index}',
        'clause_type': str(raw.get('clause_type') or 'Unspecified'),
        'category': raw.get('category'),
        'risk_level': risk_level,
        'clause_text': str(raw.get('clause_text') or ''),
        'location': _parse_location(raw.get('line_number')),
        'issue_description': str(raw.get('issue_description') or ''),
        'workaround': str(raw.get('workaround') or ''),
        'references': clause_refs,
    }


def _parse_location(raw: Any) -> Record:
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


# References

def _reference_id(url: str) -> str:
    """Stable id derived from the URL, so the same source is stored once."""
    return 'ref_' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith('www.') else host


def get_reference(ref_id: str) -> Optional[Record]:
    with _lock:
        _ensure_loaded()
        return copy.deepcopy(_find('references', ref_id))


def resolve_references(clause: Record) -> List[Record]:
    """Expand a stored clause's {ref_id, snippet} links into full references for display.
    Links to references that no longer exist are skipped."""
    resolved = []
    with _lock:
        _ensure_loaded()
        for link in clause.get('references') or []:
            ref = _find('references', link.get('ref_id', ''))
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


def set_reference_verification(ref_id: str, verification: str) -> Record:
    _require_choice(verification, VERIFICATION_STATES, 'verification')
    with _lock:
        _ensure_loaded()
        return _update('references', ref_id, {'verification': verification})


# tickets

def create_ticket(owner_id: str, analysis_id: str, subject: str, body: str,
                  clause_id: Optional[str] = None) -> Record:
    """Open a ticket on one of the owner's analyses, with its first message."""
    subject = _require_text(subject, 'subject')
    body = _require_text(body, 'body')
    with _lock:
        _ensure_loaded()
        _require('users', owner_id)
        analysis = _require('analyses', analysis_id)
        if analysis['owner_id'] != owner_id:
            raise ValueError(f"Analysis {analysis_id} does not belong to {owner_id}")
        if clause_id is not None and not any(c['id'] == clause_id for c in analysis['clauses']):
            raise LookupError(f"Clause {clause_id} not found in analysis {analysis_id}")
        created_at = _now()
        record = {
            'id': _next_id('tickets'),
            'owner_id': owner_id,
            'assignee_id': None,
            'analysis_id': analysis_id,
            'clause_id': clause_id,
            'subject': subject,
            'status': 'open',
            'messages': [_new_message(1, owner_id, body, created_at)],
            'created_at': created_at,
        }
        return _append('tickets', record)


def add_ticket_message(ticket_id: str, author_id: str, body: str) -> Record:
    """Append a message to a ticket. Returns the updated ticket."""
    body = _require_text(body, 'body')
    with _lock:
        _ensure_loaded()
        ticket = _require('tickets', ticket_id)
        _require('users', author_id)
        messages = copy.deepcopy(ticket['messages'])
        messages.append(_new_message(len(messages) + 1, author_id, body, _now()))
        return _update('tickets', ticket_id, {'messages': messages})


def update_ticket(ticket_id: str, status: Optional[str] = None,
                  assignee_id: Optional[str] = None) -> Record:
    """Change status and/or assign to an admin. None leaves a field unchanged."""
    changes: Record = {}
    if status is not None:
        changes['status'] = _require_choice(status, TICKET_STATUSES, 'status')
    with _lock:
        _ensure_loaded()
        _require('tickets', ticket_id)
        if assignee_id is not None:
            if _require('users', assignee_id)['role'] != 'admin':
                raise ValueError(f"Assignee {assignee_id} is not an admin")
            changes['assignee_id'] = assignee_id
        return _update('tickets', ticket_id, changes)


def get_ticket(ticket_id: str) -> Optional[Record]:
    with _lock:
        _ensure_loaded()
        return copy.deepcopy(_find('tickets', ticket_id))


def list_tickets(owner_id: Optional[str] = None, assignee_id: Optional[str] = None,
                 status: Optional[str] = None) -> List[Record]:
    """Tickets, newest first, optionally filtered by owner, assignee and/or status."""
    if status is not None:
        _require_choice(status, TICKET_STATUSES, 'status')
    with _lock:
        _ensure_loaded()
        matches = [t for t in _store['tickets']
                   if (owner_id is None or t['owner_id'] == owner_id)
                   and (assignee_id is None or t['assignee_id'] == assignee_id)
                   and (status is None or t['status'] == status)]
        return _newest_first(matches)


def _new_message(number: int, author_id: str, body: str, created_at: str) -> Record:
    return {'id': f'msg_{number}', 'author_id': author_id, 'body': body, 'created_at': created_at}
