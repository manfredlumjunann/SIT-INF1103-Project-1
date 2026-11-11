# Base for the enumerations whose values are stored as text.

import enum


# An enumeration stored as its text value.
class ChoiceEnum(str, enum.Enum):

    @classmethod
    def _parse(cls, value, field: str):
        try:
            return cls(value)
        except ValueError:
            allowed = ', '.join(member.value for member in cls)
            raise ValueError(f"Invalid {field} '{value}'. Expected one of: {allowed}") from None
