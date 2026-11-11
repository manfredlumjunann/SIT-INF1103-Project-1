from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from orm_base import USER_ID_SEQUENCE
from repository import Repository, format_id, violated_constraint
from role import Role
from user import User


class UserRepository(Repository[User]):

    def create(self, username: str, password_hash: str, role: Role = Role.CUSTOMER) -> User:
        """Create a user. Usernames are unique regardless of case and stored in lowercase.
        The password must already be hashed by the caller."""
        fields = User.validated_fields(username, password_hash, role)
        taken = ValueError(f"Username already taken: {fields['username']}")
        try:
            with self._database.session() as session:
                # Checked first so a taken name does not use up an id number
                if session.scalar(select(User.id).where(User.username == fields['username'])):
                    raise taken
                number = session.scalar(select(USER_ID_SEQUENCE.next_value()))
                user = User(id=format_id('us', number), **fields)
                session.add(user)
                session.flush()
                session.refresh(user)  # loads created_at set by the database
                return user
        except IntegrityError as error:
            # Two requests registering the same name at once: the unique index decides
            if violated_constraint(error) == 'uq_users_username':
                raise taken from None
            raise

    def get(self, entity_id: str) -> Optional[User]:
        with self._database.session() as session:
            return session.get(User, entity_id)

    def find_by_username(self, username: str) -> Optional[User]:
        username = User.normalise_username(username)
        with self._database.session() as session:
            return session.scalar(select(User).where(User.username == username))

    def list(self) -> List[User]:
        """Every user, ordered by username."""
        with self._database.session() as session:
            return list(session.scalars(select(User).order_by(User.username)))
