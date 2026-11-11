class ReviewError(Exception):
    """A problem with a review request that is safe to show the user.
    reason is 'invalid' (bad input), 'forbidden' (not an admin), 'not_found' or 'conflict'."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason
