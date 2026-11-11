# Alerts for analyses that flag many clauses.
#
# AlertPolicy asks every AlertRule the same question and never checks what kind of rule it has,
# so a new kind of alert is just a new AlertRule subclass.

from typing import Sequence

from alert_rule import AlertRule
from analysis import Analysis
from user import User


class AlertPolicy:
    def __init__(self, rules: Sequence[AlertRule]) -> None:
        self._rules = list(rules)

    # Run the first rule that matches.
    def evaluate(self, analysis: Analysis, owner: User) -> None:
        total = len(analysis.clauses)
        for rule in self._rules:
            if rule.matches(total):
                rule.notify(analysis, owner)
                return
