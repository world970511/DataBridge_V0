"""
알림 시스템 단위 테스트.

테스트 대상:
    - 이벤트 패턴 매칭 (fnmatch 기반 와일드카드)
    - Sender 팩토리 (get_sender)
    - WebhookSender: HMAC 서명, JSON 포맷
    - SlackSender: Block Kit 포맷
    - TeamsSender: MessageCard 포맷
    - emit_event: 예외 전파 없음 (silent failure)
    - 구독 캐시 무효화
"""

import hashlib
import hmac
import json
from unittest.mock import patch, MagicMock

import pytest

# ── 이벤트 상수 ──

from notifications.events import (
    FILE_REGISTERED, FILE_LOADED, FILE_FAILED, FILE_DELETED,
    MART_CREATED, MART_FAILED,
    JOB_COMPLETED, JOB_FAILED,
    APPROVAL_REQUESTED, APPROVAL_RESOLVED,
    EVENT_PATTERNS, CHANNELS,
)


class TestEventConstants:
    """이벤트 상수 정의 검증."""

    def test_event_format(self):
        """모든 이벤트 상수가 'category.action' 형식인지 확인."""
        events = [
            FILE_REGISTERED, FILE_LOADED, FILE_FAILED, FILE_DELETED,
            MART_CREATED, MART_FAILED,
            JOB_COMPLETED, JOB_FAILED,
            APPROVAL_REQUESTED, APPROVAL_RESOLVED,
        ]
        for event in events:
            parts = event.split(".")
            assert len(parts) == 2, f"{event} is not in 'category.action' format"
            assert parts[0], f"{event} has empty category"
            assert parts[1], f"{event} has empty action"

    def test_event_patterns_contains_wildcards(self):
        """EVENT_PATTERNS에 와일드카드 패턴이 포함되어 있는지 확인."""
        assert "*" in EVENT_PATTERNS
        assert "file.*" in EVENT_PATTERNS
        assert "job.*" in EVENT_PATTERNS
        assert "mart.*" in EVENT_PATTERNS
        assert "approval.*" in EVENT_PATTERNS

    def test_channels(self):
        """지원 채널 목록 확인."""
        assert "webhook" in CHANNELS
        assert "slack" in CHANNELS
        assert "teams" in CHANNELS


# ── 패턴 매칭 ──

from notifications.dispatcher import _match_pattern


class TestPatternMatching:
    """fnmatch 기반 이벤트 패턴 매칭 테스트."""

    def test_exact_match(self):
        assert _match_pattern("file.loaded", "file.loaded") is True

    def test_exact_no_match(self):
        assert _match_pattern("file.loaded", "file.failed") is False

    def test_wildcard_all(self):
        """'*'는 모든 이벤트에 매치."""
        assert _match_pattern("*", "file.loaded") is True
        assert _match_pattern("*", "job.failed") is True
        assert _match_pattern("*", "anything") is True

    def test_category_wildcard(self):
        """'file.*'는 file 카테고리의 모든 이벤트에 매치."""
        assert _match_pattern("file.*", "file.loaded") is True
        assert _match_pattern("file.*", "file.failed") is True
        assert _match_pattern("file.*", "file.deleted") is True

    def test_category_wildcard_no_cross(self):
        """'file.*'는 다른 카테고리에 매치하지 않음."""
        assert _match_pattern("file.*", "job.completed") is False
        assert _match_pattern("file.*", "mart.created") is False

    def test_job_wildcard(self):
        assert _match_pattern("job.*", "job.completed") is True
        assert _match_pattern("job.*", "job.failed") is True
        assert _match_pattern("job.*", "file.loaded") is False

    def test_mart_wildcard(self):
        assert _match_pattern("mart.*", "mart.created") is True
        assert _match_pattern("mart.*", "mart.failed") is True
        assert _match_pattern("mart.*", "job.failed") is False


# ── Sender 팩토리 ──

from notifications.senders import get_sender
from notifications.senders.base import BaseSender
from notifications.senders.webhook import WebhookSender
from notifications.senders.slack import SlackSender
from notifications.senders.teams import TeamsSender


class TestSenderFactory:
    """get_sender() 팩토리 테스트."""

    def test_get_webhook_sender(self):
        sender = get_sender("webhook")
        assert isinstance(sender, WebhookSender)
        assert isinstance(sender, BaseSender)

    def test_get_slack_sender(self):
        sender = get_sender("slack")
        assert isinstance(sender, SlackSender)
        assert isinstance(sender, BaseSender)

    def test_get_teams_sender(self):
        sender = get_sender("teams")
        assert isinstance(sender, TeamsSender)
        assert isinstance(sender, BaseSender)

    def test_unknown_channel_raises(self):
        with pytest.raises(KeyError):
            get_sender("unknown_channel")


# ── WebhookSender ──

class TestWebhookSender:
    """WebhookSender 동작 테스트."""

    @patch("notifications.senders.webhook.get_settings")
    @patch("notifications.senders.webhook.requests.post")
    def test_successful_send(self, mock_post, mock_settings):
        """200 응답 시 success=True."""
        mock_settings.return_value.notification.timeout = 10
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        sender = WebhookSender()
        result = sender.send(
            target="https://example.com/hook",
            event_type="file.loaded",
            payload={"table": "sales", "rows": 100},
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["error"] is None

        # 요청 검증
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["timeout"] == 10
        headers = call_kwargs.kwargs["headers"]
        assert headers["X-DataBridge-Event"] == "file.loaded"
        assert "X-DataBridge-Signature" not in headers

    @patch("notifications.senders.webhook.get_settings")
    @patch("notifications.senders.webhook.requests.post")
    def test_hmac_signature(self, mock_post, mock_settings):
        """secret 설정 시 HMAC 서명 헤더 포함."""
        mock_settings.return_value.notification.timeout = 10
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        sender = WebhookSender()
        payload = {"table": "sales"}
        secret = "my_secret_key"

        sender.send(
            target="https://example.com/hook",
            event_type="file.loaded",
            payload=payload,
            secret=secret,
        )

        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs["headers"]
        assert "X-DataBridge-Signature" in headers
        sig_header = headers["X-DataBridge-Signature"]
        assert sig_header.startswith("sha256=")

        # 서명 검증
        body_bytes = call_kwargs.kwargs["data"]
        expected_sig = hmac.new(
            secret.encode("utf-8"), body_bytes, hashlib.sha256
        ).hexdigest()
        assert sig_header == f"sha256={expected_sig}"

    @patch("notifications.senders.webhook.get_settings")
    @patch("notifications.senders.webhook.requests.post")
    def test_http_error(self, mock_post, mock_settings):
        """500 응답 시 success=False."""
        mock_settings.return_value.notification.timeout = 10
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.ok = False
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        sender = WebhookSender()
        result = sender.send(
            target="https://example.com/hook",
            event_type="file.loaded",
            payload={},
        )

        assert result["success"] is False
        assert result["status_code"] == 500

    @patch("notifications.senders.webhook.get_settings")
    @patch("notifications.senders.webhook.requests.post")
    def test_connection_error(self, mock_post, mock_settings):
        """네트워크 에러 시 success=False, status_code=None."""
        import requests

        mock_settings.return_value.notification.timeout = 10
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        sender = WebhookSender()
        result = sender.send(
            target="https://unreachable.example.com/hook",
            event_type="file.loaded",
            payload={},
        )

        assert result["success"] is False
        assert result["status_code"] is None
        assert "Connection refused" in result["error"]


# ── SlackSender ──

class TestSlackSender:
    """SlackSender 메시지 포맷 테스트."""

    @patch("notifications.senders.slack.get_settings")
    @patch("notifications.senders.slack.requests.post")
    def test_block_kit_format(self, mock_post, mock_settings):
        """Slack 메시지가 Block Kit 형식인지 확인."""
        mock_settings.return_value.notification.timeout = 10
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        sender = SlackSender()
        sender.send(
            target="https://hooks.slack.com/services/XXX",
            event_type="job.failed",
            payload={"job": "daily_refresh", "error": "timeout"},
        )

        call_kwargs = mock_post.call_args
        body = json.loads(call_kwargs.kwargs["data"])

        assert "text" in body
        assert "blocks" in body
        assert len(body["blocks"]) >= 1

        # Block Kit section 구조 확인
        first_block = body["blocks"][0]
        assert first_block["type"] == "section"
        assert "text" in first_block
        assert first_block["text"]["type"] == "mrkdwn"

    @patch("notifications.senders.slack.get_settings")
    @patch("notifications.senders.slack.requests.post")
    def test_payload_fields_in_message(self, mock_post, mock_settings):
        """payload 필드가 Slack 메시지에 포함되는지 확인."""
        mock_settings.return_value.notification.timeout = 10
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        sender = SlackSender()
        sender.send(
            target="https://hooks.slack.com/services/XXX",
            event_type="file.loaded",
            payload={"table": "my_table", "rows": 500},
        )

        call_kwargs = mock_post.call_args
        body = json.loads(call_kwargs.kwargs["data"])

        # payload 필드가 blocks 내 텍스트에 포함
        detail_block = body["blocks"][1]
        detail_text = detail_block["text"]["text"]
        assert "table" in detail_text
        assert "my_table" in detail_text
        assert "rows" in detail_text


# ── TeamsSender ──

class TestTeamsSender:
    """TeamsSender MessageCard 포맷 테스트."""

    @patch("notifications.senders.teams.get_settings")
    @patch("notifications.senders.teams.requests.post")
    def test_message_card_format(self, mock_post, mock_settings):
        """Teams 메시지가 MessageCard 형식인지 확인."""
        mock_settings.return_value.notification.timeout = 10
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        sender = TeamsSender()
        sender.send(
            target="https://outlook.webhook.office.com/XXX",
            event_type="mart.created",
            payload={"mart": "mart_daily_sales", "rows": 1000},
        )

        call_kwargs = mock_post.call_args
        body = json.loads(call_kwargs.kwargs["data"])

        assert body["@type"] == "MessageCard"
        assert "themeColor" in body
        assert "summary" in body
        assert "sections" in body


# ── emit_event 통합 ──

from notifications.dispatcher import emit_event, invalidate_cache


class TestEmitEvent:
    """emit_event() 동작 테스트."""

    @patch("notifications.dispatcher.get_settings")
    def test_disabled_does_nothing(self, mock_settings):
        """notification.enabled=False 시 아무 작업 없음."""
        mock_settings.return_value.notification.enabled = False

        # DB 조회 없이 바로 리턴되어야 함
        emit_event("file.loaded", {"table": "test"})
        # 예외 없이 정상 리턴

    @patch("notifications.dispatcher._get_active_subscriptions")
    @patch("notifications.dispatcher.get_settings")
    def test_no_subscriptions(self, mock_settings, mock_get_subs):
        """구독이 없으면 스레드를 생성하지 않음."""
        mock_settings.return_value.notification.enabled = True
        mock_get_subs.return_value = []

        emit_event("file.loaded", {"table": "test"})
        # 예외 없이 정상 리턴

    @patch("notifications.dispatcher.threading.Thread")
    @patch("notifications.dispatcher._get_active_subscriptions")
    @patch("notifications.dispatcher.get_settings")
    def test_matching_creates_threads(self, mock_settings, mock_get_subs, mock_thread):
        """매칭되는 구독이 있으면 Thread를 생성하고 start()."""
        mock_settings.return_value.notification.enabled = True
        mock_get_subs.return_value = [
            {"id": 1, "event_pattern": "file.*", "channel": "webhook",
             "target": "https://example.com", "secret": None, "display_name": "test"},
            {"id": 2, "event_pattern": "job.*", "channel": "slack",
             "target": "https://slack.com", "secret": None, "display_name": "slack"},
        ]

        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        emit_event("file.loaded", {"table": "sales"})

        # file.* 매칭: 1개 스레드만 생성
        assert mock_thread.call_count == 1
        mock_thread_instance.start.assert_called_once()

    @patch("notifications.dispatcher._get_active_subscriptions")
    @patch("notifications.dispatcher.get_settings")
    def test_exception_does_not_propagate(self, mock_settings, mock_get_subs):
        """emit_event 내부 예외가 호출자에게 전파되지 않음."""
        mock_settings.return_value.notification.enabled = True
        mock_get_subs.side_effect = Exception("DB connection failed")

        # 예외가 전파되지 않아야 함
        emit_event("file.loaded", {"table": "test"})


class TestCacheInvalidation:
    """구독 캐시 무효화 테스트."""

    def test_invalidate_cache_resets(self):
        """invalidate_cache() 호출 후 캐시가 초기화."""
        import notifications.dispatcher as d

        # 캐시에 더미 데이터 설정
        d._cached_subscriptions = [{"id": 1}]
        d._cache_timestamp = 9999999999.0

        invalidate_cache()

        assert d._cached_subscriptions is None
        assert d._cache_timestamp == 0.0
