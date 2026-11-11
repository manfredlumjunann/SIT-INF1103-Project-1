"""Registration, login and the admin account."""

import logging
import os

from werkzeug.security import check_password_hash, generate_password_hash

from data_manager import DataManager
from entities import USERNAME_PATTERN, Role, User

logger = logging.getLogger('contract-analyzer')


class AuthError(Exception):
    """A sign-in or registration problem that can be shown to the user.
    `reason` is 'invalid' (bad input), 'conflict' (already exists) or 'unauthorized'."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class AuthService:
    MIN_PASSWORD_LENGTH = 8
    USERNAME_RULES_MESSAGE = "Username must be 3-30 characters using letters, numbers, '.', '_' or '-'."
    LOGIN_FAILED_MESSAGE = 'Incorrect username or password.'

    def __init__(self, data: DataManager) -> None:
        self._data = data

    def register(self, username: str, password: str) -> User:
        """Create a customer account. Registration never creates an admin."""
        username = username.strip().lower()
        if not USERNAME_PATTERN.match(username):
            raise AuthError(self.USERNAME_RULES_MESSAGE, 'invalid')
        if len(password) < self.MIN_PASSWORD_LENGTH:
            raise AuthError(f'Password must be at least {self.MIN_PASSWORD_LENGTH} characters.', 'invalid')
        if self._data.users.find_by_username(username):
            raise AuthError('That username is already taken. Please choose another.', 'conflict')
        try:
            return self._data.users.create(username, generate_password_hash(password))
        except ValueError as error:
            raise AuthError(str(error), 'invalid') from error

    def authenticate(self, username: str, password: str) -> User:
        if not username.strip() or not password:
            raise AuthError('Please enter your username and password.', 'invalid')
        user = self._data.users.find_by_username(username)
        if user is None or not check_password_hash(user.password_hash, password):
            raise AuthError(self.LOGIN_FAILED_MESSAGE, 'unauthorized')
        return user

    def seed_admin_from_env(self) -> None:
        """Create the admin account named by ADMIN_USERNAME / ADMIN_PASSWORD, if it does not exist
        yet. Registration only ever creates customers, so this is how an admin is made."""
        username = (os.getenv('ADMIN_USERNAME') or '').strip().lower()
        password = os.getenv('ADMIN_PASSWORD') or ''
        if not username and not password:
            return
        if not username or len(password) < self.MIN_PASSWORD_LENGTH:
            logger.warning("Admin account not created: set both ADMIN_USERNAME and an ADMIN_PASSWORD "
                           "of at least %d characters in .env", self.MIN_PASSWORD_LENGTH)
            return

        existing = self._data.users.find_by_username(username)
        if existing is not None:
            if not existing.is_admin():
                # Never change an existing account's role or password from here
                logger.warning("ADMIN_USERNAME '%s' already belongs to a %s account; it was left unchanged",
                               username, existing.role.value)
            return

        try:
            self._data.users.create(username, generate_password_hash(password), role=Role.ADMIN)
        except ValueError as error:
            logger.warning("Admin account not created: %s", error)
            return
        logger.info("Created admin account '%s'", username)
