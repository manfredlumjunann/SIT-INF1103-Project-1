from document_extractor import DocumentExtractor


class TxtExtractor(DocumentExtractor):
    @property
    def suffix(self) -> str:
        return '.txt'

    def extract(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as file:
            return file.read()
