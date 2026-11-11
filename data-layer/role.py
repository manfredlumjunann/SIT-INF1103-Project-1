from choice_enum import ChoiceEnum


class Role(ChoiceEnum):
    CUSTOMER = 'customer'
    ADMIN = 'admin'

    @classmethod
    def parse(cls, value) -> 'Role':
        return cls._parse(value, 'role')
