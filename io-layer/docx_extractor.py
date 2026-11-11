from document_extractor import DocumentExtractor

try:
    from docx import Document
except ImportError:
    Document = None


class DocxExtractor(DocumentExtractor):
    @property
    def suffix(self) -> str:
        return '.docx'

    def extract(self, path: str) -> str:
        if Document is None:
            raise ImportError("python-docx not installed")
        return '\n\n'.join(p.text for p in Document(path).paragraphs if p.text.strip())
