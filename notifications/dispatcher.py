"""
알림 이벤트 디스패처.

emit_event()를 통해 이벤트를 발행하면, DB의 notification_subscriptions에서
매칭되는 구독을 조회하여 비동기(threading)로 알림을 전송합니다.

설계 원칙:
    - 알림 실패가 메인 파이프라인을 중단시키지 않음 (silent failure)
    - 비동기 전송으로 호출자의 응답 시간에 영향 없음
    - 구독 목록은 60초 TTL 캐싱으로 매번 DB 조회 방지
    - _audit.py 패턴과 동일한 예외 처리 방식

사용법:
    from notifications.dispatcher import emit_event
    emit_event("file.loaded", {"table": "sales", "rows": 1000})
"""

import json
import logging
import threading
import time
from fnmatch import fnmatch
from typing import Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)

# 구독 캐시 (TTL 60초)
_cache_lock = threading.Lock()
_cached_subscriptions: Optional[list] = None
_cache_timestamp: float = 0.0
_CACHE_TTL = 60.0


def emit_event(event_type: str, payload: dict) -> None:
    """
    이벤트를 발행하여 매칭되는 구독에 알림 전송.

    모든 예외를 내부에서 처리하며, 호출자에게 예외를 전파하지 않습니다.
    전송은 별도 스레드에서 비동기로 수행됩니다.

    Args:
        event_type: 이벤트 타입 문자열 (예: 'file.loaded', 'job.failed').
        payload: 이벤트 데이터 딕셔너리.
    """
    try:
        settings = get_settings()
        if not settings.notification.enabled:
            return

        subscriptions = _get_active_subscriptions()
        if not subscriptions:
            return

        matched = [
            sub for sub in subscriptions
            if _match_pattern(sub["event_pattern"], event_type)
        ]

        if not matched:
            return

        logger.debug(
            f"Event '{event_type}' matched {len(matched)} subscription(s)"
        )

        for sub in matched:
            t = threading.Thread(
                target=_send_and_log,
                args=(sub, event_type, payload),
                daemon=True,
            )
            t.start()

    except Exception:
        logger.exception(f"Failed to dispatch event: {event_type}")


def _match_pattern(pattern: str, event_type: str) -> bool:
    """
    구독 패턴과 이벤트 타입 매칭.

    fnmatch 사용: 'file.*' → 'file.loaded' 매치, '*' → 모든 이벤트 매치.
    """
    return fnmatch(event_type, pattern)


def _get_active_subscriptions() -> list:
    """
    활성화된 구독 목록을 조회 (60초 TTL 캐싱).

    DB 조회 실패 시 빈 리스트를 반환합니다.
    """
    global _cached_subscriptions, _cache_timestamp

    now = time.time()
    with _cache_lock:
        if _cached_subscriptions is not None and (now - _cache_timestamp) < _CACHE_TTL:
            return _cached_subscriptions

    try:
        from db.connection import execute_query

        rows = execute_query(
            """
            SELECT id, event_pattern, channel, target, secret, display_name
            FROM notification_subscriptions
            WHERE enabled = TRUE
            """,
        )
        result = [dict(row) for row in rows] if rows else []

        with _cache_lock:
            _cached_subscriptions = result
            _cache_timestamp = time.time()

        return result

    except Exception:
        logger.exception("Failed to load notification subscriptions")
        return []


def invalidate_cache() -> None:
    """구독 캐시를 무효화. 구독 변경 시 호출."""
    global _cached_subscriptions, _cache_timestamp
    with _cache_lock:
        _cached_subscriptions = None
        _cache_timestamp = 0.0


def _send_and_log(subscription: dict, event_type: str, payload: dict) -> None:
    """
    단일 구독에 알림을 전송하고 결과를 notification_log에 기록.

    재시도 로직 포함: max_retries만큼 재시도 후 최종 결과를 기록합니다.
    """
    settings = get_settings()
    max_retries = settings.notification.max_retries
    retry_delay = settings.notification.retry_delay

    channel = subscription["channel"]
    target = subscription["target"]
    secret = subscription.get("secret")
    sub_id = subscription["id"]

    result = None
    start_ms = time.time()

    try:
        from notifications.senders import get_sender
        sender = get_sender(channel)
    except KeyError:
        logger.warning(f"Unknown notification channel: {channel}")
        _log_delivery(sub_id, event_type, channel, target, payload, {
            "success": False, "status_code": None, "error": f"Unknown channel: {channel}",
        }, 0)
        return

    for attempt in range(1 + max_retries):
        result = sender.send(target, event_type, payload, secret)

        if result["success"]:
            break

        if attempt < max_retries:
            logger.debug(
                f"Notification retry {attempt + 1}/{max_retries} "
                f"for {channel}:{event_type}"
            )
            time.sleep(retry_delay)

    elapsed_ms = int((time.time() - start_ms) * 1000)

    if result and result["success"]:
        logger.debug(f"Notification sent: {channel} → {event_type}")
    else:
        error = result["error"] if result else "Unknown error"
        logger.warning(
            f"Notification failed: {channel} → {event_type}: {error}"
        )

    _log_delivery(sub_id, event_type, channel, target, payload, result, elapsed_ms)


def _log_delivery(
    subscription_id: int,
    event_type: str,
    channel: str,
    target: str,
    payload: dict,
    result: dict,
    elapsed_ms: int,
) -> None:
    """전송 결과를 notification_log 테이블에 기록."""
    try:
        from db.connection import get_cursor

        payload_json = json.dumps(payload, ensure_ascii=False, default=str)

        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO notification_log
                    (subscription_id, event_type, channel, target,
                     payload, status, error_message, response_code, elapsed_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    subscription_id,
                    event_type,
                    channel,
                    target,
                    payload_json,
                    "success" if result.get("success") else "failed",
                    result.get("error"),
                    result.get("status_code"),
                    elapsed_ms,
                ),
            )
    except Exception:
        logger.exception("Failed to log notification delivery")
