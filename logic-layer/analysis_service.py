# Runs one analysis end to end: detect clauses, research case law, improve workarounds, save, alert.

import logging
from datetime import datetime
from typing import Dict, List, Tuple

from ai_manager import AIManager
from alert_policy import AlertPolicy
from analysis import Analysis
from analysis_status import AnalysisStatus
from data_manager import DataManager
from firecrawl_researcher import FirecrawlResearcher
from recovery_session import RecoverySession
from risk_level import RiskLevel
from user import User

logger = logging.getLogger('contract-analyzer')


class AnalysisService:
    def __init__(self, data: DataManager, ai: AIManager, researcher: FirecrawlResearcher,
                 alerts: AlertPolicy) -> None:
        self._data = data
        self._ai = ai
        self._researcher = researcher
        self._alerts = alerts

    # Returns (analysis, was_cached). The same contract and context gives the saved result
    # back. Results with no clauses are never reused, so one bad AI answer cannot get stuck.
    def analyze(self, owner: User, filename: str, contract_text: str,
                context: str) -> Tuple[Analysis, bool]:
        contract_hash = self._data.analyses.compute_contract_hash(contract_text, context)
        cached = self._data.analyses.find_by_hash(owner.id, contract_hash)
        if cached and cached.clauses:
            logger.info("Returning saved analysis %s (same contract and context)", cached.id)
            return cached, True

        analysis = self._data.analyses.create(owner.id, filename, contract_text, context)
        try:
            clauses = self._run_pipeline(contract_text, context)
            saved = self._data.analyses.complete(analysis.id, clauses)
            self._alerts.evaluate(saved, owner)
        except Exception:
            try:
                self._data.analyses.fail(analysis.id)
            except Exception as save_error:
                logger.error("Could not mark analysis %s as failed: %s", analysis.id, save_error)
            raise
        return saved, False

    # Mark analyses left 'processing' by a restart as failed (there is one worker, so they
    # can never finish). Returns how many there were.
    def recover_interrupted(self) -> int:
        stale = self._data.analyses.list(status=AnalysisStatus.PROCESSING)
        for analysis in stale:
            self._data.analyses.fail(analysis.id)
        return len(stale)

    def health_report(self) -> Dict:
        working, failed = self._ai.probe_models()
        return {
            'status': 'healthy' if working else 'degraded',
            'openrouter_configured': self._ai.is_configured,
            'firecrawl_configured': self._researcher.is_configured,
            'working_models': working,
            'failed_models': failed,
            'primary_model': self._ai.primary_model,
            'timestamp': datetime.now().isoformat(),
        }

    def _run_pipeline(self, contract_text: str, context: str) -> List[Dict]:
        # One recovery session per analysis, so the engineers are alerted at most once
        recovery = RecoverySession()

        logger.info("Detecting clauses with AI...")
        clauses = self._ai.detect_clauses(contract_text, context, recovery)
        if not clauses:
            return []

        logger.info("Researching case law...")
        for clause in clauses:
            clause['legal_references'] = self._researcher.research(clause)

        logger.info("Generating detailed workarounds...")
        clauses = self._ai.improve_workarounds(clauses, recovery)

        return [self._with_defaults(clause) for clause in clauses if isinstance(clause, dict)]

    # Fill in missing fields; an unknown risk level becomes MEDIUM.
    @staticmethod
    def _with_defaults(clause: Dict) -> Dict:
        clause.setdefault('issue_description', 'Potential risk identified')
        clause.setdefault('workaround', 'Consult legal counsel')
        clause.setdefault('legal_references', [])
        clause.setdefault('line_number', None)
        try:
            clause['risk_level'] = RiskLevel.parse(clause.get('risk_level') or '').value
        except ValueError:
            clause['risk_level'] = RiskLevel.MEDIUM.value
        return clause
