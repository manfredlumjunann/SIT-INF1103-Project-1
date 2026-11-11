"""Small functions shared by the entities."""

from datetime import datetime, timezone
from typing import Type

from choice_enum import ChoiceEnum


def in_list(column: str, choices: Type[ChoiceEnum]) -> str:
    """SQL for a CHECK constraint limiting a column to the values of an enumeration."""
    values = ', '.join(f"'{member.value}'" for member in choices)
    return f'{column} IN ({values})'


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def require_text(value, field: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text
