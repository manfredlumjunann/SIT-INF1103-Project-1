"""Alerts for analyses that flag many clauses.

AlertPolicy asks every AlertRule the same question and never checks what kind of rule it has,
so a new kind of alert is just a new AlertRule subclass."""

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from entities import Analysis, RiskLevel, User
from notifications import Notifier


class AlertRule(ABC):
    def __init__(self, notifier: Notifier, recipient: str) -> None:
        self._notifier = notifier
        self._recipient = recipient

    @abstractmethod
    def matches(self, total_flagged: int) -> bool:
        """Should an analysis with this many flagged clauses trigger the rule?"""

    def notify(self, analysis: Analysis, owner: User) -> None:
        counts = analysis.counts()
        message = (
            "Contract clause analysis completed\n"
            f"User ID: {owner.id}\n"
            f"Username: {owner.username}\n"
            f"Total clause flagged: {len(analysis.clauses)}\n"
            f"High risk: {counts[RiskLevel.HIGH]}\n"
            f"Medium Risk: {counts[RiskLevel.MEDIUM]}\n"
            f"Low risk: {counts[RiskLevel.LOW]}"
        )
        self._notifier.send(message, self._recipient)


class ClauseCountAlertRule(AlertRule):
    """Matches when the number of flagged clauses is between min_flagged and max_flagged."""

    def __init__(self, notifier: Notifier, recipient: str, min_flagged: int,
                 max_flagged: Optional[int] = None) -> None:
        super().__init__(notifier, recipient)
        self._min_flagged = min_flagged
        self._max_flagged = max_flagged

    def matches(self, total_flagged: int) -> bool:
        return (total_flagged >= self._min_flagged
                and (self._max_flagged is None or total_flagged <= self._max_flagged))


class AlertPolicy:
    def __init__(self, rules: Sequence[AlertRule]) -> None:
        self._rules = list(rules)

    def evaluate(self, analysis: Analysis, owner: User) -> None:
        """Run the first rule that matches."""
        total = len(analysis.clauses)
        for rule in self._rules:
            if rule.matches(total):
                rule.notify(analysis, owner)
                return
