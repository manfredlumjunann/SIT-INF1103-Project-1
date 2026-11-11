from choice_enum import ChoiceEnum


class Verification(ChoiceEnum):
    UNVERIFIED = 'unverified'
    VERIFIED_SG = 'verified_sg'
    REJECTED = 'rejected'

    @classmethod
    def parse(cls, value) -> 'Verification':
        return cls._parse(value, 'verification')
