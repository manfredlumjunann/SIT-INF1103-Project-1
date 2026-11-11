from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from clause import Clause
from clause_comment import ClauseComment
from orm_base import COMMENT_ID_SEQUENCE
from repository import Repository, format_id


# Comments are addressed the way the app shows them: by analysis id and clause position.
class CommentRepository(Repository[ClauseComment]):

    def get(self, entity_id: str) -> Optional[ClauseComment]:
        with self._database.session() as session:
            return session.get(ClauseComment, entity_id)

    def list(self) -> List[ClauseComment]:
        with self._database.session() as session:
            return list(session.scalars(select(ClauseComment).order_by(ClauseComment.id)))

    # Add the comment to the clause, or replace the one it already has.
    # Raises LookupError if the clause does not exist and ValueError if the text is not valid.
    def save(self, analysis_id: str, position: int, author_id: str, text: str) -> ClauseComment:
        with self._database.session() as session:
            clause = self._clause(session, analysis_id, position)
            if clause.comment is None:
                number = session.scalar(select(COMMENT_ID_SEQUENCE.next_value()))
                clause.comment = ClauseComment(format_id('cm', number), author_id, text)
            else:
                clause.comment.revise(author_id, text)
            session.flush()
            return clause.comment

    # Remove the clause's comment. Returns False if it had none.
    def delete(self, analysis_id: str, position: int) -> bool:
        with self._database.session() as session:
            clause = self._clause(session, analysis_id, position)
            had_comment = clause.comment is not None
            clause.comment = None
            return had_comment

    @staticmethod
    def _clause(session: Session, analysis_id: str, position: int) -> Clause:
        clause = session.scalar(select(Clause).options(selectinload(Clause.comment))
                                .where(Clause.analysis_id == analysis_id, Clause.position == position))
        if clause is None:
            raise LookupError(f"clauses record not found: {analysis_id} clause {position}")
        return clause
