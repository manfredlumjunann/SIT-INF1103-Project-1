from dataclasses import dataclass

from clause import Clause


@dataclass(frozen=True)
class ClauseMatch:
    """The same clause found in both analyses."""
    first: Clause
    second: Clause
    matched_on: str  # 'text' or 'type'

    @property
    def risk_change(self) -> str:
        """How the risk in the second analysis compares with the first: 'higher', 'lower' or 'same'."""
        difference = self.second.risk_level.rank - self.first.risk_level.rank
        if difference == 0:
            return 'same'
        return 'higher' if difference > 0 else 'lower'
