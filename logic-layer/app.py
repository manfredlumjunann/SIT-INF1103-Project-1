# Entry point: gunicorn imports `app` from this module.

import logging
import os

from dotenv import load_dotenv
from flask import Flask

from ai_manager import AIManager
from alert_policy import AlertPolicy
from analysis_comparer import AnalysisComparer
from analysis_service import AnalysisService
from auth_service import AuthService
from clause_count_alert_rule import ClauseCountAlertRule
from data_manager import DataManager
from docx_extractor import DocxExtractor
from firecrawl_researcher import FirecrawlResearcher
from io_manager import IOManager
from pdf_extractor import PdfExtractor
from review_service import ReviewService
from telegram_notifier import TelegramNotifier
from txt_extractor import TxtExtractor

logger = logging.getLogger('contract-analyzer')


class Application:
    # Telegram chats for big analyses; ALERT_MEDIUM_CHAT_ID / ALERT_HIGH_CHAT_ID override them
    DON_ID = '667740965'
    MAN_ID = '1116849976'
    MEDIUM_ALERT_RANGE = (7, 8)
    HIGH_ALERT_FROM = 9

    # Create every manager, connect them and return the Flask app.
    @staticmethod
    def build() -> Flask:
        data = DataManager.from_env()
        logger.info("Loaded saved records: %s", data.init_storage())

        notifier = TelegramNotifier.from_env()
        alerts = AlertPolicy([
            ClauseCountAlertRule(notifier,
                                 os.getenv('ALERT_MEDIUM_CHAT_ID') or Application.DON_ID,
                                 *Application.MEDIUM_ALERT_RANGE),
            ClauseCountAlertRule(notifier,
                                 os.getenv('ALERT_HIGH_CHAT_ID') or Application.MAN_ID,
                                 Application.HIGH_ALERT_FROM),
        ])
        ai = AIManager(os.getenv('OPENROUTER_API_KEY'), approval=notifier)
        analyses = AnalysisService(data, ai, FirecrawlResearcher(os.getenv('FIRECRAWL_API_KEY')), alerts)

        interrupted = analyses.recover_interrupted()
        if interrupted:
            logger.info("Marked %d interrupted analyses as failed", interrupted)

        auth = AuthService(data)
        auth.seed_admin_from_env()

        return IOManager(
            auth, analyses, ReviewService(data), AnalysisComparer(), data,
            extractors=[PdfExtractor(), DocxExtractor(), TxtExtractor()],
            secret_key=os.getenv('SECRET_KEY'),
        ).flask_app


load_dotenv()
# Details go to the server log, never into responses sent to the browser
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')

app = Application.build()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
