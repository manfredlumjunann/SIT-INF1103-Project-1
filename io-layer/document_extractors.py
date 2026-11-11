"""Turns an uploaded contract file into plain text.

DocumentExtractor is the common contract; one subclass per file format overrides extract().
Supporting a new format means adding a subclass, not changing existing code.
"""

from abc import ABC, abstractmethod
from pathlib import Path

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    from docx import Document
except ImportError:
    Document = None


class DocumentExtractor(ABC):
    @property
    @abstractmethod
    def suffix(self) -> str:
        """File extension this extractor reads, lower case, including the dot."""

    def can_handle(self, path: str) -> bool:
        return Path(path).suffix.lower() == self.suffix

    @abstractmethod
    def extract(self, path: str) -> str:
        """The text of the document at path."""


class PdfExtractor(DocumentExtractor):
    @property
    def suffix(self) -> str:
        return '.pdf'

    def extract(self, path: str) -> str:
        if PyPDF2 is None:
            raise ImportError("PyPDF2 not installed")
        pages = []
        with open(path, 'rb') as file:
            for page in PyPDF2.PdfReader(file).pages:
                page_text = page.extract_text()
                if page_text:
                    pages.append(page_text)
        return '\n\n'.join(pages)


class DocxExtractor(DocumentExtractor):
    @property
    def suffix(self) -> str:
        return '.docx'

    def extract(self, path: str) -> str:
        if Document is None:
            raise ImportError("python-docx not installed")
        return '\n\n'.join(p.text for p in Document(path).paragraphs if p.text.strip())


class TxtExtractor(DocumentExtractor):
    @property
    def suffix(self) -> str:
        return '.txt'

    def extract(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as file:
            return file.read()
