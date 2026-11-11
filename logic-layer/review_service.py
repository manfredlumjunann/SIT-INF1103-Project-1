# The review feature: an admin comments on the clauses of a user's finished analysis.

from analysis_status import AnalysisStatus
from clause_comment import ClauseComment
from data_manager import DataManager
from review_error import ReviewError
from user import User


class ReviewService:
    def __init__(self, data: DataManager) -> None:
        self._data = data

    # Add or replace the comment on one clause. Only admins may comment.
    def comment_on_clause(self, reviewer: User, analysis_id: str, position: int,
                          text: str) -> ClauseComment:
        self._require_reviewable(reviewer, analysis_id)
        try:
            return self._data.comments.save(analysis_id, position, reviewer.id, text)
        except LookupError:
            raise ReviewError('Clause not found', 'not_found') from None
        except ValueError as error:
            raise ReviewError(str(error), 'invalid') from None

    # Delete the comment on one clause. Only admins may do this.
    def remove_comment(self, reviewer: User, analysis_id: str, position: int) -> None:
        self._require_reviewable(reviewer, analysis_id)
        try:
            removed = self._data.comments.delete(analysis_id, position)
        except LookupError:
            raise ReviewError('Clause not found', 'not_found') from None
        if not removed:
            raise ReviewError('This clause has no comment', 'not_found')

    def _require_reviewable(self, reviewer: User, analysis_id: str) -> None:
        if not reviewer.is_admin():
            raise ReviewError('Only administrators can comment on clauses.', 'forbidden')
        analysis = self._data.analyses.get(analysis_id)
        if analysis is None:
            raise ReviewError('Analysis not found', 'not_found')
        if analysis.status is not AnalysisStatus.DONE:
            raise ReviewError('Only finished analyses can be commented on.', 'conflict')
