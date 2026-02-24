"""
Microsoft Teams Connector (Incoming Webhook) 전송 채널.

Teams의 MessageCard 형식으로 메시지를 구성하여 전송합니다.
target은 Teams Incoming Webhook URL입니다.
"""

import json
import logging
from typing import Optional

import requests

from config.settings import get_settings
from notifications.senders.base import BaseSender

logger = logging.getLogger(__name__)

# 이벤트 카테고리별 테마 색상 (Teams themeColor)
_COLOR_MAP = {
    "file.loaded": "28a745",      # 녹색
    "file.registered": "17a2b8",  # 청색
    "file.failed": "dc3545",      # 빨강
    "file.deleted": "6c757d",     # 회색
    "mart.created": "28a745",
    "mart.failed": "dc3545",
    "job.completed": "28a745",
    "job.failed": "dc3545",
    "approval.requested": "ffc107",  # 노랑
    "approval.resolved": "28a745",
}


def _get_color(event_type: str) -> str:
    return _COLOR_MAP.get(event_type, "007bff")  # 파랑 default


def _build_facts(payload: dict) -> list:
    """Teams MessageCard의 facts 배열 구성."""
    return [{"name": k, "value": str(v)} for k, v in payload.items()]


class TeamsSender(BaseSender):
    """Microsoft Teams Incoming Webhook 전송."""

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
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": _get_color(event_type),
            "summary": f"[DataBridge] {event_type}",
            "sections": [
                {
                    "activityTitle": f"**[DataBridge]** {event_type}",
                    "facts": _build_facts(payload),
                    "markdown": True,
                }
            ],
        }

        try:
            resp = requests.post(
                target,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=timeout,
            )
            return {
                "success": 200 <= resp.status_code < 300,
                "status_code": resp.status_code,
                "error": None if resp.ok else resp.text[:500],
            }
        except requests.RequestException as e:
            return {"success": False, "status_code": None, "error": str(e)[:500]}
