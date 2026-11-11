"""Contract Clause Analyzer - application entry point (gunicorn imports `app` from here).

Application is the only place that knows which concrete classes exist: it builds each manager
and hands it the collaborators it needs.
"""

import logging
import os

from dotenv import load_dotenv
from flask import Flask

from ai_manager import AIManager
from alert_policy import AlertPolicy, ClauseCountAlertRule
from analysis_comparer import AnalysisComparer
from analysis_service import AnalysisService
from auth_service import AuthService
from data_manager import DataManager
from document_extractors import DocxExtractor, PdfExtractor, TxtExtractor
from firecrawl_researcher import FirecrawlResearcher
from io_manager import IOManager
from notifications import TelegramNotifier

logger = logging.getLogger('contract-analyzer')


class Application:
    # Telegram chats told about analyses with many flagged clauses (override with environment variables)
    DON_ID = '667740965'
    MAN_ID = '1116849976'
    MEDIUM_ALERT_RANGE = (7, 8)
    HIGH_ALERT_FROM = 9

    @staticmethod
    def build() -> Flask:
        """Connect to storage, wire the managers together and return the Flask application."""
        data = DataManager.from_env()
        counts = data.init_storage()
        logger.info("Loaded saved records: %s", counts)

        notifier = TelegramNotifier.from_env()
        alerts = AlertPolicy([
            ClauseCountAlertRule(notifier,
                                 os.getenv('ALERT_MEDIUM_CHAT_ID') or Application.DON_ID,
                                 *Application.MEDIUM_ALERT_RANGE),
            ClauseCountAlertRule(notifier,
                                 os.getenv('ALERT_HIGH_CHAT_ID') or Application.MAN_ID,
                                 Application.HIGH_ALERT_FROM),
        ])
        analyses = AnalysisService(
            data,
            AIManager(os.getenv('OPENROUTER_API_KEY'), approval=notifier),
            FirecrawlResearcher(os.getenv('FIRECRAWL_API_KEY')),
            alerts)
        interrupted = analyses.recover_interrupted()
        if interrupted:
            logger.info("Marked %d interrupted analyses as failed", interrupted)

        auth = AuthService(data)
        auth.seed_admin_from_env()

        return IOManager(
            auth, analyses, AnalysisComparer(), data,
            extractors=[PdfExtractor(), DocxExtractor(), TxtExtractor()],
            secret_key=os.getenv('SECRET_KEY'),
        ).flask_app


load_dotenv()
# Server-side log (visible with `docker compose logs`). Error details go here,
# never into responses sent to the browser.
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')

app = Application.build()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
