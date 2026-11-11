"""Notification channels.

Notifier and ApprovalChannel are interfaces: they only say what a channel must be able to do.
TelegramNotifier is the one implementation, so Telegram could be swapped for e-mail or Slack
without touching the code that raises alerts or asks an engineer for a decision.
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Optional, Tuple

import requests

logger = logging.getLogger('contract-analyzer')


class Notifier(ABC):
    """Can deliver a message to a recipient."""

    @abstractmethod
    def send(self, message: str, recipient: str) -> None:
        """Deliver message to recipient. Must not raise when delivery fails."""


class ApprovalChannel(ABC):
    """Can tell the engineering team about a problem and wait for their decision."""

    @abstractmethod
    def request_decision(self, prompt: str) -> str:
        """Show prompt to the team and block until they choose; returns 'accept' or 'reject'."""

    @abstractmethod
    def announce(self, message: str) -> None:
        """Post a progress message to the team."""


class TelegramNotifier(Notifier, ApprovalChannel):
    API_URL = 'https://api.telegram.org/bot{token}/{method}'
    DEFAULT_GROUP_CHAT_ID = '-5212170665'

    def __init__(self, token: Optional[str], group_chat_id: str = DEFAULT_GROUP_CHAT_ID) -> None:
        self._token = token
        self._group_chat_id = group_chat_id

    @classmethod
    def from_env(cls) -> 'TelegramNotifier':
        return cls(os.getenv('TELEGRAM_BOT_TOKEN'),
                   os.getenv('TELEGRAM_GROUP_CHAT_ID') or cls.DEFAULT_GROUP_CHAT_ID)

    def _url(self, method: str) -> str:
        return self.API_URL.format(token=self._token, method=method)

    # Notifier

    def send(self, message: str, recipient: str) -> None:
        if not self._token:
            logger.warning("TELEGRAM_BOT_TOKEN not set; skipping notification")
            return
        try:
            results = requests.get(self._url('sendMessage'),
                                   params={'chat_id': recipient, 'text': message}, timeout=10)
            logger.info("Telegram notification sent: %s", results.json())
        except Exception as exc:
            logger.error("Failed to send Telegram notification: %s", exc)

    # ApprovalChannel

    def announce(self, message: str) -> None:
        if not self._token:
            logger.warning("TELEGRAM_BOT_TOKEN not set; skipping error notification")
            return
        results = requests.post(self._url('sendMessage'),
                                json={'chat_id': self._group_chat_id, 'text': message})
        logger.info("Telegram group message sent: %s", results.json())

    def request_decision(self, prompt: str) -> str:
        if not self._token:
            raise RuntimeError("Telegram bot token not configured")
        last_update_id = self._latest_update_id()
        self._send_error_alert(prompt)
        choice, _ = self._wait_for_reply(last_update_id)
        return choice

    def _latest_update_id(self) -> int:
        try:
            initial = requests.get(self._url('getUpdates'), params={'offset': -1}, timeout=15).json()
        except (requests.RequestException, ValueError):
            initial = {}
        if initial.get('result'):
            return max(item['update_id'] for item in initial['result'])
        return 0

    def _send_error_alert(self, error_message: str) -> None:
        payload = {
            'chat_id': self._group_chat_id,
            'text': f"⚠️ AI Processing Error\n\n{error_message}",
            'reply_markup': {
                'inline_keyboard': [[
                    {'text': '✅ Fix', 'callback_data': 'accept'},
                    {'text': '❌ Stop', 'callback_data': 'reject'},
                ]]
            },
        }
        try:
            results = requests.get(self._url('sendMessage'), json=payload, timeout=10)
            logger.info("Telegram error notification sent: %s", results.json())
        except Exception as exc:
            logger.error("Failed to send Telegram error notification: %s", exc)

    def _wait_for_reply(self, last_update_id: int) -> Tuple[str, int]:
        """Block until someone presses a button in the engineering group."""
        logger.info("Telegram listener ACTIVE. Waiting for button press...")
        while True:
            payload = {'offset': last_update_id + 1, 'timeout': 10, 'allowed_updates': ['callback_query']}
            try:
                results = requests.get(self._url('getUpdates'), params=payload, timeout=15).json()
                if results.get('ok') and results.get('result'):
                    for update in results['result']:
                        last_update_id = max(last_update_id, update['update_id'])
                        callback = update.get('callback_query')
                        if not callback:
                            continue
                        if str(callback['message']['chat']['id']) != str(self._group_chat_id):
                            continue
                        choice = callback.get('data')
                        # Acknowledge the press by removing the buttons
                        requests.post(self._url('editMessageReplyMarkup'), json={
                            'chat_id': callback['message']['chat']['id'],
                            'message_id': callback['message']['message_id'],
                            'reply_markup': {'inline_keyboard': []},
                        })
                        if choice:
                            return choice, last_update_id
            except requests.RequestException as error:
                logger.error("Error checking Telegram: %s", error)
            except ValueError as error:
                logger.error("Invalid Telegram response: %s", error)
            time.sleep(1)
