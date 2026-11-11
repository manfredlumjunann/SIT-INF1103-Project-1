class AuthError(Exception):
    """A registration or login problem that is safe to show the user.
    reason is 'invalid' (bad input), 'conflict' (already exists) or 'unauthorized'."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason
