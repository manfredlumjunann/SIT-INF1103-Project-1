from choice_enum import ChoiceEnum


class AnalysisStatus(ChoiceEnum):
    PROCESSING = 'processing'
    DONE = 'done'
    FAILED = 'failed'

    @classmethod
    def parse(cls, value) -> 'AnalysisStatus':
        return cls._parse(value, 'status')
