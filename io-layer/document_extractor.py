from abc import ABC, abstractmethod
from pathlib import Path


class DocumentExtractor(ABC):
    """Turns an uploaded contract file into plain text.

    One subclass per file format overrides extract(). Supporting a new format means adding a
    subclass, not changing existing code."""

    @property
    @abstractmethod
    def suffix(self) -> str:
        """File extension this extractor reads, lower case, including the dot."""

    def can_handle(self, path: str) -> bool:
        return Path(path).suffix.lower() == self.suffix

    @abstractmethod
    def extract(self, path: str) -> str:
        """The text of the document at path."""
