"""
범용 Webhook 전송 채널.

HTTP POST로 JSON payload를 전송합니다.
secret이 설정된 경우 HMAC-SHA256 서명을 X-DataBridge-Signature 헤더에 추가합니다.
"""

import hashlib
import hmac
import json
import logging
from typing import Optional

import requests

from config.settings import get_settings
from notifications.senders.base import BaseSender

logger = logging.getLogger(__name__)


class WebhookSender(BaseSender):
    """범용 HTTP POST Webhook 전송."""

    def send(
        self,
        target: str,
        event_type: str,
        payload: dict,
        secret: Optional[str] = None,
    ) -> dict:
        settings = get_settings()
        timeout = settings.notification.timeout

        body = {
            "event": event_type,
            "data": payload,
        }
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-DataBridge-Event": event_type,
        }

        if secret:
            sig = hmac.new(
                secret.encode("utf-8"), body_bytes, hashlib.sha256
            ).hexdigest()
            headers["X-DataBridge-Signature"] = f"sha256={sig}"

        try:
            resp = requests.post(
                target, data=body_bytes, headers=headers, timeout=timeout,
            )
            return {
                "success": 200 <= resp.status_code < 300,
                "status_code": resp.status_code,
                "error": None if resp.ok else resp.text[:500],
            }
        except requests.RequestException as e:
            return {"success": False, "status_code": None, "error": str(e)[:500]}
