import re
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from orm_base import Base
from entity_helpers import in_list, iso_utc, require_text
from role import Role

USERNAME_PATTERN = re.compile(r'^[a-z0-9._-]{3,30}$')


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint(f"username ~ '{USERNAME_PATTERN.pattern}'", name='username_format'),
        CheckConstraint(in_list('role', Role), name='role'),
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

    # Check and clean the fields for a new user; raises ValueError if any is invalid.
    # The password must already be hashed.
    @classmethod
    def validated_fields(cls, username, password_hash, role) -> dict:
        username = cls.normalise_username(username)
        if not USERNAME_PATTERN.match(username):
            raise ValueError("Username must be 3-30 characters: letters, numbers, '.', '_' or '-'")
        return {
            'username': username,
            'password_hash': require_text(password_hash, 'password_hash'),
            'role': Role.parse(role),
        }

    @property
    def role(self) -> Role:
        return Role(self._role)

    # Promote or demote the user; anything that is not a known role is refused.
    @role.setter
    def role(self, value) -> None:
        self._role = Role.parse(value).value

    @property
    def created_at_iso(self) -> str:
        return iso_utc(self.created_at)

    def is_admin(self) -> bool:
        return self.role is Role.ADMIN
