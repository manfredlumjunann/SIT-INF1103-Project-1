from abc import ABC, abstractmethod


# Can tell the engineers about a problem and wait for their answer.
class ApprovalChannel(ABC):
    # Show the prompt and wait; returns 'accept' or 'reject'.
    @abstractmethod
    def request_decision(self, prompt: str) -> str:
        pass

    # Post a progress message to the engineers.
    @abstractmethod
    def announce(self, message: str) -> None:
        pass
