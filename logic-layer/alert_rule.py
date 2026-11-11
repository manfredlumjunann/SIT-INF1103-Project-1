from abc import ABC, abstractmethod

from analysis import Analysis
from notifier import Notifier
from risk_level import RiskLevel
from user import User


class AlertRule(ABC):
    """One way of deciding that an analysis deserves an alert, and who hears about it."""

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
