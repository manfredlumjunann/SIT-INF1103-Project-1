from abc import ABC, abstractmethod


class ApprovalChannel(ABC):
    """Can tell the engineers about a problem and wait for their answer."""

    @abstractmethod
    def request_decision(self, prompt: str) -> str:
        """Show the prompt and wait; returns 'accept' or 'reject'."""

    @abstractmethod
    def announce(self, message: str) -> None:
        """Post a progress message to the engineers."""
