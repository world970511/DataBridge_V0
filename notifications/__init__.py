"""
DataBridge 알림 시스템.

이벤트 기반 알림을 외부 채널(Webhook, Slack, Teams)로 전송합니다.

사용법:
    from notifications.dispatcher import emit_event
    emit_event("file.loaded", {"table": "sales", "rows": 100})
"""
