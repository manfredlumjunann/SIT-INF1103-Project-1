"""IO Manager: the HTTP boundary. It reads requests, checks input, calls the managers and shapes
the JSON the browser renders. It holds no business rules and never touches the database directly."""

import functools
import logging
import os
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from flask import Flask, Response, g, jsonify, request, session
from werkzeug.utils import secure_filename

from analysis_comparer import AnalysisComparer, Comparison
from analysis_service import AnalysisService
from auth_service import AuthError, AuthService
from data_manager import DataManager
from document_extractors import DocumentExtractor
from entities import Analysis, AnalysisStatus, Clause, RiskLevel, User

logger = logging.getLogger('contract-analyzer')

AUTH_ERROR_STATUS = {'invalid': 400, 'conflict': 409, 'unauthorized': 401}


class IOManager:
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    SESSION_HOURS = 8

    def __init__(self, auth: AuthService, analyses: AnalysisService, comparer: AnalysisComparer,
                 data: DataManager, extractors: Sequence[DocumentExtractor],
                 secret_key: Optional[str] = None) -> None:
        self._auth = auth
        self._analyses = analyses
        self._comparer = comparer
        self._data = data
        self._extractors = list(extractors)
        self._flask = Flask(__name__)
        self._configure(secret_key)
        self._register_routes()

    @property
    def flask_app(self) -> Flask:
        return self._flask

    # Setup

    def _configure(self, secret_key: Optional[str]) -> None:
        self._flask.config['MAX_CONTENT_LENGTH'] = self.MAX_UPLOAD_BYTES
        # SECRET_KEY signs the login cookie; without a fixed key everyone is signed out on restart
        self._flask.secret_key = secret_key or secrets.token_hex(32)
        if not secret_key:
            logger.warning("SECRET_KEY not set in .env - using a temporary key; sessions reset on restart")
        self._flask.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE='Lax',
            PERMANENT_SESSION_LIFETIME=timedelta(hours=self.SESSION_HOURS),
        )

    def _register_routes(self) -> None:
        routes = [
            ('/api/auth/register', 'POST', self.register, None),
            ('/api/auth/login', 'POST', self.login, None),
            ('/api/auth/me', 'GET', self.current_user, None),
            ('/api/auth/logout', 'POST', self.logout, None),
            ('/api/analyze', 'POST', self.analyze, self._login_required),
            ('/api/analyses', 'GET', self.list_analyses, self._login_required),
            ('/api/analyses/<analysis_id>', 'GET', self.get_analysis, self._login_required),
            ('/api/analyses/<analysis_id>', 'DELETE', self.delete_analysis, self._login_required),
            ('/api/compare', 'GET', self.compare, self._login_required),
            ('/api/analyses/<analysis_id>/compare-options', 'GET', self.compare_options, self._login_required),
            ('/api/admin/users', 'GET', self.admin_users, self._admin_required),
            ('/api/admin/analyses', 'GET', self.admin_analyses, self._admin_required),
            ('/api/health', 'GET', self.health, None),
        ]
        for path, method, handler, guard in routes:
            view = guard(handler) if guard else handler
            self._flask.add_url_rule(path, f'{handler.__name__}_{method}', view, methods=[method])

    # Who is signed in

    def _session_user(self) -> Optional[User]:
        user_id = session.get('user_id')
        if not user_id:
            return None
        user = self._data.users.get(user_id)
        if user is None:
            session.clear()
        return user

    @staticmethod
    def _start_session(user: User) -> None:
        # Clear first so a session id set before login cannot be reused afterwards
        session.clear()
        session.permanent = True
        session['user_id'] = user.id

    def _login_required(self, view: Callable) -> Callable:
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            user = self._session_user()
            if user is None:
                return jsonify({'error': 'Please sign in to continue.'}), 401
            g.user = user
            return view(*args, **kwargs)
        return wrapper

    def _admin_required(self, view: Callable) -> Callable:
        """Signed in as an admin. Everyone else gets 403, so admin data is protected by the
        server and not by which page the browser happens to load."""
        @functools.wraps(view)
        @self._login_required
        def wrapper(*args, **kwargs):
            if not g.user.is_admin():
                return jsonify({'error': 'This area is for administrators only.'}), 403
            return view(*args, **kwargs)
        return wrapper

    @staticmethod
    def _json_fields(*names: str) -> Dict[str, str]:
        payload = request.get_json(silent=True) or {}
        return {name: str(payload.get(name) or '') for name in names}

    @staticmethod
    def _public_user(user: User) -> Dict:
        return {'id': user.id, 'username': user.username, 'role': user.role.value}

    # Authentication routes

    def register(self) -> Response:
        fields = self._json_fields('username', 'password')
        try:
            user = self._auth.register(fields['username'], fields['password'])
        except AuthError as error:
            return jsonify({'error': str(error)}), AUTH_ERROR_STATUS[error.reason]
        self._start_session(user)
        return jsonify({'user': self._public_user(user)}), 201

    def login(self) -> Response:
        fields = self._json_fields('username', 'password')
        try:
            user = self._auth.authenticate(fields['username'], fields['password'])
        except AuthError as error:
            return jsonify({'error': str(error)}), AUTH_ERROR_STATUS[error.reason]
        self._start_session(user)
        return jsonify({'user': self._public_user(user)})

    def current_user(self) -> Response:
        """Who is signed in; 401 if nobody is."""
        user = self._session_user()
        if user is None:
            return jsonify({'error': 'Not signed in'}), 401
        return jsonify({'user': self._public_user(user)})

    def logout(self) -> Response:
        """End the session. POST (not GET) so other sites cannot sign users out via a link."""
        session.clear()
        return jsonify({'message': 'Signed out'})

    # Analysis routes

    def analyze(self) -> Response:
        try:
            if 'contract' not in request.files:
                return jsonify({'error': 'No contract file provided'}), 400
            file = request.files['contract']
            context = request.form.get('context', '').strip()
            if file.filename == '':
                return jsonify({'error': 'No file selected'}), 400

            filename = secure_filename(file.filename)
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
                file.save(tmp.name)
                tmp_path = tmp.name
            try:
                logger.info("Extracting text from document...")
                contract_text = self._extract_text(tmp_path)
                if not contract_text.strip():
                    return jsonify({'error': 'Could not extract text from document'}), 400
                analysis, cached = self._analyses.analyze(g.user, filename, contract_text, context)
                return jsonify(self._analysis_view(analysis, cached))
            finally:
                os.unlink(tmp_path)
        except Exception as error:
            return self._analysis_error_response(error)

    def _extract_text(self, path: str) -> str:
        for extractor in self._extractors:
            if extractor.can_handle(path):
                return extractor.extract(path)
        raise ValueError(f"Unsupported format: {Path(path).suffix.lower()}")

    @staticmethod
    def _analysis_error_response(error: Exception) -> Response:
        """Log the full error server-side and send the user a generic message.
        Raw exception text can contain file paths or AI provider responses, so it is never returned."""
        reference = secrets.token_hex(4)
        if isinstance(error, RuntimeError):
            # AI failover exhausted
            logger.warning("AI service unavailable [ref %s]: %s", reference, error)
            return jsonify({'error': 'The AI service is currently unavailable. Please try again in a few minutes.',
                            'reference': reference}), 503
        logger.exception("Analysis failed [ref %s]", reference, exc_info=error)
        return jsonify({'error': 'Something went wrong while analysing the contract. '
                                 f'Please try again. (Reference: {reference})',
                        'reference': reference}), 500

    def list_analyses(self) -> Response:
        """History list, newest first. Optional filters: ?status=done|failed|processing&risk=high|medium|low"""
        try:
            status = request.args.get('status') or None
            risk = request.args.get('risk') or None
            analyses = self._data.analyses.list(
                owner_id=g.user.id,
                status=AnalysisStatus.parse(status) if status else None,
                risk_level=RiskLevel.parse(risk) if risk else None,
            )
        except ValueError as error:
            return jsonify({'error': str(error)}), 400
        return jsonify({'analyses': [self._analysis_summary(a) for a in analyses]})

    def get_analysis(self, analysis_id: str) -> Response:
        """Reopen a saved analysis without calling the AI."""
        analysis = self._readable_analysis(analysis_id)
        if analysis is None:
            return jsonify({'error': 'Analysis not found'}), 404
        return jsonify(self._analysis_view(analysis))

    def delete_analysis(self, analysis_id: str) -> Response:
        """Permanently delete one of the current user's analyses."""
        analysis = self._owned_analysis(analysis_id)
        if analysis is None:
            return jsonify({'error': 'Analysis not found'}), 404
        if analysis.status is AnalysisStatus.PROCESSING:
            # The pipeline still needs this record to save its result
            return jsonify({'error': 'This analysis is still in progress. Try again once it has finished.'}), 409
        self._data.analyses.delete(analysis_id)
        return jsonify({'deleted': analysis_id})

    def _owned_analysis(self, analysis_id: str) -> Optional[Analysis]:
        """The analysis if it exists and belongs to the current user, else None."""
        analysis = self._data.analyses.get(analysis_id)
        if analysis is None or not analysis.is_owned_by(g.user.id):
            return None
        return analysis

    def _readable_analysis(self, analysis_id: str) -> Optional[Analysis]:
        """The analysis if the current user may view it: their own, or any analysis for an admin."""
        if g.user.is_admin():
            return self._data.analyses.get(analysis_id)
        return self._owned_analysis(analysis_id)

    # Compare routes

    def compare(self) -> Response:
        """Compare two finished analyses: ?a=<id>&b=<id>. Customers can compare their own;
        an admin can compare any two, including analyses of different users."""
        id_a = request.args.get('a') or ''
        id_b = request.args.get('b') or ''
        if not id_a or not id_b:
            return jsonify({'error': 'Choose two analyses to compare.'}), 400
        if id_a == id_b:
            return jsonify({'error': 'Choose two different analyses to compare.'}), 400

        first = self._readable_analysis(id_a)
        second = self._readable_analysis(id_b)
        if first is None or second is None:
            return jsonify({'error': 'Analysis not found'}), 404
        if first.status is not AnalysisStatus.DONE or second.status is not AnalysisStatus.DONE:
            return jsonify({'error': 'Only finished analyses can be compared.'}), 409
        if request.args.get('chronological') and not self._comparer.is_older(first, second):
            first, second = second, first
        return jsonify(self._comparison_view(self._comparer.compare(first, second)))

    def compare_options(self, analysis_id: str) -> Response:
        """The current user's other finished analyses that this one can be compared with,
        newest first, split into earlier analyses of the same file and the rest."""
        analysis = self._owned_analysis(analysis_id)
        if analysis is None:
            return jsonify({'error': 'Analysis not found'}), 404
        if analysis.status is not AnalysisStatus.DONE:
            return jsonify({'error': 'Only finished analyses can be compared.'}), 409
        finished = self._data.analyses.list(owner_id=g.user.id, status=AnalysisStatus.DONE)
        same_file, others = self._comparer.split_by_file(analysis, finished)
        return jsonify({'same_file': [self._analysis_summary(a) for a in same_file],
                        'others': [self._analysis_summary(a) for a in others]})

    # Admin routes: read-only view of every user's analyses

    def admin_users(self) -> Response:
        """Every account with how many analyses it has saved."""
        analysis_counts: Dict[str, int] = {}
        for analysis in self._data.analyses.list():
            analysis_counts[analysis.owner_id] = analysis_counts.get(analysis.owner_id, 0) + 1
        users = []
        for user in self._data.users.list():
            entry = self._public_user(user)
            entry['created_at'] = user.created_at_iso
            entry['analysis_count'] = analysis_counts.get(user.id, 0)
            users.append(entry)
        return jsonify({'users': users})

    def admin_analyses(self) -> Response:
        """Analyses of one user (?user=<id>) or of everyone, newest first, labelled with their owner."""
        owner_id = request.args.get('user') or None
        if owner_id is not None and self._data.users.get(owner_id) is None:
            return jsonify({'error': 'User not found'}), 404
        usernames = {user.id: user.username for user in self._data.users.list()}
        analyses = []
        for analysis in self._data.analyses.list(owner_id=owner_id):
            entry = self._analysis_summary(analysis)
            entry['owner_id'] = analysis.owner_id
            entry['owner_username'] = usernames.get(analysis.owner_id, 'Deleted user')
            analyses.append(entry)
        return jsonify({'analyses': analyses})

    def health(self) -> Response:
        """Health check endpoint with model availability test."""
        return jsonify(self._analyses.health_report())

    # Shaping JSON for the browser

    @staticmethod
    def _counts_view(analysis: Analysis) -> Dict[str, int]:
        return {level.label: count for level, count in analysis.counts().items()}

    @staticmethod
    def _clause_view(clause: Clause) -> Dict:
        """One stored clause in the shape the frontend renders."""
        return {
            'id': f'cl_{clause.position}',
            'clause_type': clause.clause_type,
            'risk_level': clause.risk_level.value,
            'clause_text': clause.clause_text,
            'location': {
                'page': clause.location_page,
                'clause_ref': clause.location_clause_ref,
                'raw': clause.location_raw,
            },
            'issue_description': clause.issue_description,
            'workaround': clause.workaround,
            'legal_references': [{
                'title': link.reference.title,
                'url': link.reference.url,
                'summary': link.snippet,
                'domain': link.reference.domain,
                'verification': link.reference.verification.value,
            } for link in clause.references],
            'line_number': clause.location_raw,
        }

    def _analysis_view(self, analysis: Analysis, cached: bool = False) -> Dict:
        """Full saved analysis in the shape the frontend renders."""
        clauses = [self._clause_view(clause) for clause in analysis.clauses]
        view = {
            'id': analysis.id,
            'filename': analysis.filename,
            'context': analysis.context,
            'status': analysis.status.value,
            'counts': self._counts_view(analysis),
            'clauses': clauses,
            'total_flagged': len(clauses),
            'timestamp': analysis.created_at_iso,
            'cached': cached,
        }
        if not clauses:
            view['message'] = ('This analysis failed before any clauses were saved.'
                               if analysis.status is AnalysisStatus.FAILED
                               else 'No problematic clauses detected')
        return view

    def _analysis_summary(self, analysis: Analysis) -> Dict:
        """Lightweight history entry (no contract text or clauses)."""
        counts = self._counts_view(analysis)
        return {
            'id': analysis.id,
            'filename': analysis.filename,
            'context': analysis.context,
            'status': analysis.status.value,
            'counts': counts,
            'total_flagged': sum(counts.values()),
            'timestamp': analysis.created_at_iso,
        }

    def _comparison_view(self, comparison: Comparison) -> Dict:
        usernames: Dict[str, str] = {}
        sides = {}
        for side, analysis in (('a', comparison.first), ('b', comparison.second)):
            if analysis.owner_id not in usernames:
                owner = self._data.users.get(analysis.owner_id)
                usernames[analysis.owner_id] = owner.username if owner else 'Deleted user'
            sides[side] = self._analysis_summary(analysis)
            sides[side]['owner_username'] = usernames[analysis.owner_id]
        return {
            'a': sides['a'],
            'b': sides['b'],
            'same_contract': comparison.same_contract,
            'in_both': [{
                'a': self._clause_view(match.first),
                'b': self._clause_view(match.second),
                'matched_on': match.matched_on,
                'risk_change': match.risk_change,
            } for match in comparison.in_both],
            'only_a': [self._clause_view(clause) for clause in comparison.only_first],
            'only_b': [self._clause_view(clause) for clause in comparison.only_second],
        }
