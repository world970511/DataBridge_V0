"""
Slack Incoming Webhook 전송 채널.

Slack의 Block Kit 형식으로 메시지를 구성하여 전송합니다.
target은 Slack Incoming Webhook URL입니다.
"""

import json
import logging
from typing import Optional

import requests

from config.settings import get_settings
from notifications.senders.base import BaseSender

logger = logging.getLogger(__name__)

# 이벤트 카테고리별 이모지
_EMOJI_MAP = {
    "file.loaded": "\u2705",       # ✅
    "file.registered": "\U0001F4C1",  # 📁
    "file.failed": "\u274C",       # ❌
    "file.deleted": "\U0001F5D1\uFE0F",  # 🗑️
    "mart.created": "\U0001F4CA",  # 📊
    "mart.failed": "\u274C",       # ❌
    "job.completed": "\u2705",     # ✅
    "job.failed": "\U0001F6A8",    # 🚨
    "approval.requested": "\U0001F514",  # 🔔
    "approval.resolved": "\u2705", # ✅
}


def _get_emoji(event_type: str) -> str:
    """이벤트 타입에 맞는 이모지 반환."""
    return _EMOJI_MAP.get(event_type, "\U0001F4E2")  # 📢 default


def _build_blocks(event_type: str, payload: dict) -> list:
    """Slack Block Kit 메시지 구성."""
    emoji = _get_emoji(event_type)
    header_text = f"{emoji} *[DataBridge]* `{event_type}`"

    fields = []
    for key, value in payload.items():
        fields.append(f"*{key}:* {value}")
    detail_text = "\n".join(fields) if fields else "_데이터 없음_"

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": header_text},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": detail_text},
        },
    ]
    return blocks


class SlackSender(BaseSender):
    """Slack Incoming Webhook 전송."""

    def send(
        self,
        target: str,
        event_type: str,
        payload: dict,
        secret: Optional[str] = None,
    ) -> dict:
        settings = get_settings()
        timeout = settings.notification.timeout

        emoji = _get_emoji(event_type)
        fallback_text = f"{emoji} [DataBridge] {event_type}"

        body = {
            "text": fallback_text,
            "blocks": _build_blocks(event_type, payload),
        }

        try:
            resp = requests.post(
                target,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=timeout,
            )
            return {
                "success": resp.status_code == 200,
                "status_code": resp.status_code,
                "error": None if resp.ok else resp.text[:500],
            }
        except requests.RequestException as e:
            return {"success": False, "status_code": None, "error": str(e)[:500]}
