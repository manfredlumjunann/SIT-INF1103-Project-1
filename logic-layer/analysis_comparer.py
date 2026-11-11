"""Compares two finished analyses (the "compare" feature)."""

from dataclasses import dataclass
from typing import Dict, List, Tuple

from entities import Analysis, Clause

# Tried in this order: the same quoted text is a surer sign of the same clause than the same name
MATCH_FIELDS = ('clause_text', 'clause_type')


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


class AnalysisComparer:
    def compare(self, first: Analysis, second: Analysis) -> Comparison:
        """Pair up the clauses of two analyses. No AI is involved, so the same two analyses always
        give the same result. Each clause is used once; repeated keys pair up in flagged order."""
        pairs: List[Tuple[int, int, str]] = []
        left_first = list(range(len(first.clauses)))
        left_second = list(range(len(second.clauses)))

        for field in MATCH_FIELDS:
            waiting: Dict[str, List[int]] = {}
            for index in left_second:
                key = self._key(second.clauses[index], field)
                if key:
                    waiting.setdefault(key, []).append(index)

            unmatched = []
            matched_second = set()
            for index in left_first:
                candidates = waiting.get(self._key(first.clauses[index], field))
                if candidates:
                    other = candidates.pop(0)
                    matched_second.add(other)
                    pairs.append((index, other, field))
                else:
                    unmatched.append(index)
            left_first = unmatched
            left_second = [index for index in left_second if index not in matched_second]

        pairs.sort()
        return Comparison(
            first=first, second=second,
            in_both=[ClauseMatch(first.clauses[a], second.clauses[b],
                                 'text' if field == 'clause_text' else 'type') for a, b, field in pairs],
            only_first=[first.clauses[index] for index in left_first],
            only_second=[second.clauses[index] for index in left_second],
        )

    def split_by_file(self, analysis: Analysis,
                      candidates: List[Analysis]) -> Tuple[List[Analysis], List[Analysis]]:
        """Split candidates (other than analysis itself) into earlier runs of the same file and the rest."""
        others = [candidate for candidate in candidates if candidate.id != analysis.id]
        return ([c for c in others if self._same_file(c, analysis)],
                [c for c in others if not self._same_file(c, analysis)])

    @staticmethod
    def is_older(first: Analysis, second: Analysis) -> bool:
        """Older analysis first; when created at the same moment, ids are handed out in order."""
        if first.created_at_iso != second.created_at_iso:
            return first.created_at_iso < second.created_at_iso
        return str(first.id) <= str(second.id)

    @staticmethod
    def _key(clause: Clause, field: str) -> str:
        """Lowercase with single spaces, so case and line wrapping do not prevent a match."""
        return ' '.join(str(getattr(clause, field) or '').lower().split())

    @staticmethod
    def _same_file(first: Analysis, second: Analysis) -> bool:
        """Same filename, ignoring case: a re-run with different context or a revised contract."""
        name = str(first.filename or '').strip().lower()
        return name != '' and name == str(second.filename or '').strip().lower()
