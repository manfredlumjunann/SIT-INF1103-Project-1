from typing import Optional

from alert_rule import AlertRule
from notifier import Notifier


# Matches when the number of flagged clauses is between min_flagged and max_flagged.
class ClauseCountAlertRule(AlertRule):
    def __init__(self, notifier: Notifier, recipient: str, min_flagged: int,
                 max_flagged: Optional[int] = None) -> None:
        super().__init__(notifier, recipient)
        self._min_flagged = min_flagged
        self._max_flagged = max_flagged

    def matches(self, total_flagged: int) -> bool:
        return (total_flagged >= self._min_flagged
                and (self._max_flagged is None or total_flagged <= self._max_flagged))
