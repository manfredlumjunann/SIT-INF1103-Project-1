from choice_enum import ChoiceEnum


class RiskLevel(ChoiceEnum):
    HIGH = 'HIGH'
    MEDIUM = 'MEDIUM'
    LOW = 'LOW'

    @classmethod
    def parse(cls, value) -> 'RiskLevel':
        return cls._parse(str(value).strip().upper(), 'risk_level')

    @property
    def label(self) -> str:
        return self.value.lower()

    @property
    def rank(self) -> int:
        return {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}[self.value]
