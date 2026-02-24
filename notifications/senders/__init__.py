"""
Sender 팩토리.

channel 문자열로 적절한 BaseSender 구현체를 반환합니다.
새 채널 추가 시 이 파일의 _SENDERS에 등록하면 됩니다.
"""

from notifications.senders.base import BaseSender
from notifications.senders.webhook import WebhookSender
from notifications.senders.slack import SlackSender
from notifications.senders.teams import TeamsSender

_SENDERS: dict[str, type[BaseSender]] = {
    "webhook": WebhookSender,
    "slack": SlackSender,
    "teams": TeamsSender,
}


def get_sender(channel: str) -> BaseSender:
    """
    채널명으로 sender 인스턴스를 반환.

    Args:
        channel: 'webhook', 'slack', 'teams' 중 하나.

    Raises:
        KeyError: 지원하지 않는 채널.
    """
    cls = _SENDERS.get(channel)
    if cls is None:
        raise KeyError(f"지원하지 않는 알림 채널: {channel}")
    return cls()
