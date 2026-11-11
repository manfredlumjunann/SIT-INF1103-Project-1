from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from entity_helpers import iso_utc
from orm_base import Base


class ClauseComment(Base):
    """What an admin wrote about one flagged clause. A clause has at most one comment (clause_id is
    unique) and the comment is deleted with its clause. Customers only ever read it: the text
    changes only through revise(), which the review service allows for admins."""
    __tablename__ = 'clause_comments'

    MAX_LENGTH = 2000

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    clause_id: Mapped[int] = mapped_column(ForeignKey('clauses.id', ondelete='CASCADE'), unique=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    _text: Mapped[str] = mapped_column('text', Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    clause: Mapped['Clause'] = relationship(back_populates='comment')

    def __init__(self, id: str, author_id: str, text: str) -> None:
        self.id = id
        self.author_id = author_id
        self.text = text
        self.created_at = self.updated_at = datetime.now(timezone.utc)

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value) -> None:
        """Store the comment trimmed; blank or very long comments are refused."""
        cleaned = str(value or '').strip()
        if not cleaned:
            raise ValueError("Comment must not be empty")
        if len(cleaned) > self.MAX_LENGTH:
            raise ValueError(f"Comment must be at most {self.MAX_LENGTH} characters")
        self._text = cleaned

    @property
    def updated_at_iso(self) -> str:
        return iso_utc(self.updated_at)

    def revise(self, author_id: str, text: str) -> None:
        """Replace the text (validated by the setter) and record who changed it and when."""
        self.text = text
        self.author_id = author_id
        self.updated_at = datetime.now(timezone.utc)
