# The HTTP side of the app: reads requests, checks input, calls the managers and shapes the
# JSON the browser shows. It holds no business rules and never queries the database itself.

import functools
import logging
import os
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

from flask import Flask, Response, g, jsonify, request, session
from werkzeug.utils import secure_filename

from analysis import Analysis
from analysis_comparer import AnalysisComparer
from analysis_service import AnalysisService
from analysis_status import AnalysisStatus
from auth_error import AuthError
from auth_service import AuthService
from clause import Clause
from clause_comment import ClauseComment
from comparison import Comparison
from data_manager import DataManager
from document_extractor import DocumentExtractor
from review_error import ReviewError
from review_service import ReviewService
from risk_level import RiskLevel
from user import User

logger = logging.getLogger('contract-analyzer')

AUTH_ERROR_STATUS = {'invalid': 400, 'conflict': 409, 'unauthorized': 401}
REVIEW_ERROR_STATUS = {'invalid': 400, 'forbidden': 403, 'not_found': 404, 'conflict': 409}


class IOManager:
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    SESSION_HOURS = 8

    def __init__(self, auth: AuthService, analyses: AnalysisService, reviews: ReviewService,
                 comparer: AnalysisComparer, data: DataManager, extractors: Sequence[DocumentExtractor],
                 secret_key: Optional[str] = None) -> None:
        self._auth = auth
        self._analyses = analyses
        self._reviews = reviews
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
        # Without a fixed SECRET_KEY everyone is signed out whenever the server restarts
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
            ('/api/admin/analyses/<analysis_id>/clauses/<int:position>/comment', 'PUT',
             self.save_comment, self._admin_required),
            ('/api/admin/analyses/<analysis_id>/clauses/<int:position>/comment', 'DELETE',
             self.delete_comment, self._admin_required),
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

    # Admins only. Checked on the server, so admin data is not protected just by hiding the page.
    def _admin_required(self, view: Callable) -> Callable:
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

    # Who is signed in; 401 if nobody is.
    def current_user(self) -> Response:
        user = self._session_user()
        if user is None:
            return jsonify({'error': 'Not signed in'}), 401
        return jsonify({'user': self._public_user(user)})

    # End the session. POST (not GET) so other sites cannot sign users out via a link.
    def logout(self) -> Response:
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

    # Log the real error and send the user a generic message with a reference.
    # The raw error can contain file paths or AI provider text, so it is never returned.
    @staticmethod
    def _analysis_error_response(error: Exception) -> Response:
        reference = secrets.token_hex(4)
        if isinstance(error, RuntimeError):
            # Every AI model failed
            logger.warning("AI service unavailable [ref %s]: %s", reference, error)
            return jsonify({'error': 'The AI service is currently unavailable. Please try again in a few minutes.',
                            'reference': reference}), 503
        logger.exception("Analysis failed [ref %s]", reference, exc_info=error)
        return jsonify({'error': 'Something went wrong while analysing the contract. '
                                 f'Please try again. (Reference: {reference})',
                        'reference': reference}), 500

    # History list, newest first. Optional filters: ?status=done|failed|processing&risk=high|medium|low
    def list_analyses(self) -> Response:
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

    # Reopen a saved analysis without calling the AI.
    def get_analysis(self, analysis_id: str) -> Response:
        analysis = self._readable_analysis(analysis_id)
        if analysis is None:
            return jsonify({'error': 'Analysis not found'}), 404
        return jsonify(self._analysis_view(analysis))

    # Permanently delete one of the current user's analyses.
    def delete_analysis(self, analysis_id: str) -> Response:
        analysis = self._owned_analysis(analysis_id)
        if analysis is None:
            return jsonify({'error': 'Analysis not found'}), 404
        if analysis.status is AnalysisStatus.PROCESSING:
            # The pipeline still needs the record to save its result
            return jsonify({'error': 'This analysis is still in progress. Try again once it has finished.'}), 409
        self._data.analyses.delete(analysis_id)
        return jsonify({'deleted': analysis_id})

    # The analysis if it exists and belongs to the current user, else None.
    def _owned_analysis(self, analysis_id: str) -> Optional[Analysis]:
        analysis = self._data.analyses.get(analysis_id)
        if analysis is None or not analysis.is_owned_by(g.user.id):
            return None
        return analysis

    # The analysis if the current user may view it: their own, or any analysis for an admin.
    def _readable_analysis(self, analysis_id: str) -> Optional[Analysis]:
        if g.user.is_admin():
            return self._data.analyses.get(analysis_id)
        return self._owned_analysis(analysis_id)

    # Compare routes

    # Compare two finished analyses: ?a=<id>&b=<id>. Customers can compare their own;
    # an admin can compare any two, including analyses of different users.
    def compare(self) -> Response:
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

    # The current user's other finished analyses that this one can be compared with,
    # newest first, split into earlier analyses of the same file and the rest.
    def compare_options(self, analysis_id: str) -> Response:
        analysis = self._owned_analysis(analysis_id)
        if analysis is None:
            return jsonify({'error': 'Analysis not found'}), 404
        if analysis.status is not AnalysisStatus.DONE:
            return jsonify({'error': 'Only finished analyses can be compared.'}), 409
        finished = self._data.analyses.list(owner_id=g.user.id, status=AnalysisStatus.DONE)
        same_file, others = self._comparer.split_by_file(analysis, finished)
        return jsonify({'same_file': [self._analysis_summary(a) for a in same_file],
                        'others': [self._analysis_summary(a) for a in others]})

    # Admin routes: see every user's analyses and comment on their clauses

    # Every account with how many analyses it has saved.
    def admin_users(self) -> Response:
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

    # Analyses of one user (?user=<id>) or of everyone, newest first, labelled with their owner.
    def admin_analyses(self) -> Response:
        owner_id = request.args.get('user') or None
        if owner_id is not None and self._data.users.get(owner_id) is None:
            return jsonify({'error': 'User not found'}), 404
        usernames = {user.id: user.username for user in self._data.users.list()}
        analyses = []
        for analysis in self._data.analyses.list(owner_id=owner_id):
            entry = self._analysis_summary(analysis)
            entry['owner_id'] = analysis.owner_id
            entry['owner_username'] = usernames.get(analysis.owner_id, 'Deleted user')
            entry['commented_count'] = analysis.commented_clause_count()
            analyses.append(entry)
        return jsonify({'analyses': analyses})

    # Add or replace the admin's comment on one clause.
    def save_comment(self, analysis_id: str, position: int) -> Response:
        fields = self._json_fields('text')
        try:
            comment = self._reviews.comment_on_clause(g.user, analysis_id, position, fields['text'])
        except ReviewError as error:
            return jsonify({'error': str(error)}), REVIEW_ERROR_STATUS[error.reason]
        return jsonify({'comment': self._comment_view(comment)})

    def delete_comment(self, analysis_id: str, position: int) -> Response:
        try:
            self._reviews.remove_comment(g.user, analysis_id, position)
        except ReviewError as error:
            return jsonify({'error': str(error)}), REVIEW_ERROR_STATUS[error.reason]
        return jsonify({'deleted': True})

    # Health check endpoint with model availability test.
    def health(self) -> Response:
        return jsonify(self._analyses.health_report())

    # Shaping JSON for the browser

    @staticmethod
    def _counts_view(analysis: Analysis) -> Dict[str, int]:
        return {level.label: count for level, count in analysis.counts().items()}

    @staticmethod
    def _comment_view(comment: Optional[ClauseComment]) -> Optional[Dict]:
        if comment is None:
            return None
        return {'text': comment.text, 'updated_at': comment.updated_at_iso}

    # One stored clause in the shape the frontend renders.
    @staticmethod
    def _clause_view(clause: Clause) -> Dict:
        return {
            'id': f'cl_{clause.position}',
            'position': clause.position,
            'comment': IOManager._comment_view(clause.comment),
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

    # Full saved analysis in the shape the frontend renders.
    def _analysis_view(self, analysis: Analysis, cached: bool = False) -> Dict:
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

    # Lightweight history entry (no contract text or clauses).
    def _analysis_summary(self, analysis: Analysis) -> Dict:
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
