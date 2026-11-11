from document_extractor import DocumentExtractor

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None


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
