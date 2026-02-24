"""
알림 전송 채널의 기본 인터페이스(ABC).

모든 sender는 이 클래스를 상속하고 send() 메서드를 구현합니다.
새 채널 추가 시: senders/ 디렉토리에 파일 하나 추가 + __init__.py의 _SENDERS에 등록.
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseSender(ABC):
    """알림 전송 채널 추상 클래스."""

    @abstractmethod
    def send(
        self,
        target: str,
        event_type: str,
        payload: dict,
        secret: Optional[str] = None,
    ) -> dict:
        """
        알림을 전송.

        Args:
            target: 전송 대상 URL (Webhook URL, Slack Webhook URL 등).
            event_type: 이벤트 타입 문자열 (예: 'file.loaded').
            payload: 이벤트 데이터 딕셔너리.
            secret: HMAC 서명용 비밀 키 (webhook 전용, 선택).

        Returns:
            {"success": bool, "status_code": int|None, "error": str|None}
        """
