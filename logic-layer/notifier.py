from abc import ABC, abstractmethod


class Notifier(ABC):
    """Can deliver a message to someone."""

    @abstractmethod
    def send(self, message: str, recipient: str) -> None:
        """Deliver the message. Must not raise if delivery fails."""
