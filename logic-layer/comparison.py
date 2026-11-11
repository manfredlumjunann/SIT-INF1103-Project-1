from dataclasses import dataclass
from typing import List

from analysis import Analysis
from clause import Clause
from clause_match import ClauseMatch


@dataclass(frozen=True)
class Comparison:
    first: Analysis
    second: Analysis
    in_both: List[ClauseMatch]
    only_first: List[Clause]
    only_second: List[Clause]

    @property
    def same_contract(self) -> bool:
        return self.first.contract_text == self.second.contract_text
