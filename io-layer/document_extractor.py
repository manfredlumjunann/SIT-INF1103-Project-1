from abc import ABC, abstractmethod
from pathlib import Path


# Turns an uploaded contract file into plain text.
#
# One subclass per file format overrides extract(). Supporting a new format means adding a
# subclass, not changing existing code.
class DocumentExtractor(ABC):

    # File extension this extractor reads, lower case, including the dot.
    @property
    @abstractmethod
    def suffix(self) -> str:
        pass

    def can_handle(self, path: str) -> bool:
        return Path(path).suffix.lower() == self.suffix

    # The text of the document at path.
    @abstractmethod
    def extract(self, path: str) -> str:
        pass
