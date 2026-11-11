from abc import ABC, abstractmethod


# Can deliver a message to someone.
class Notifier(ABC):
    # Deliver the message. Must not raise if delivery fails.
    @abstractmethod
    def send(self, message: str, recipient: str) -> None:
        pass
