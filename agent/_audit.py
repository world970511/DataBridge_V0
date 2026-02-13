"""
에이전트 감사 로그(audit log) 기록 헬퍼 모듈.

AI 에이전트의 모든 주요 동작(질의 접수, SQL 생성, 실행, 오류 등)을
audit_log 테이블에 기록하여 추적·감사할 수 있도록 합니다.
watcher/loader/_utils.py의 log_file_process() 패턴을 참고하여 설계되었으며,
로그 기록 자체의 실패가 에이전트 동작에 영향을 주지 않도록 예외를 억제합니다.

audit_log 테이블 스키마:
    id              SERIAL PRIMARY KEY
    action_type     VARCHAR(50) NOT NULL   -- 'query', 'sql_generate', 'execution', 'error' 등
    user_id         VARCHAR(100)           -- 기본값 'system'
    query_text      TEXT                   -- 사용자의 원본 질의
    sql_generated   TEXT                   -- LLM이 생성한 SQL (해당 시에만)
    result_summary  TEXT                   -- 실행 결과 요약
    status          VARCHAR(20)            -- 'success', 'failed', 'pending'
    metadata        JSONB                  -- 추가 컨텍스트 (의도 분류, 소요 시간 등)
    created_at      TIMESTAMPTZ            -- 자동 생성

의존 모듈:
    - db.connection: get_cursor() — PostgreSQL 커서 획득

사용 예시:
    from agent._audit import log_action
    log_action(
        action_type="query",
        query_text="총 매출 보여줘",
        status="success",
        result_summary="총 매출: 1,234,000원",
    )
"""

import json
import logging
from typing import Optional

from db.connection import get_cursor

logger = logging.getLogger(__name__)


def log_action(
    action_type: str,
    query_text: Optional[str] = None,
    sql_generated: Optional[str] = None,
    result_summary: Optional[str] = None,
    status: str = "success",
    user_id: str = "system",
    metadata: Optional[dict] = None,
):
    """
    에이전트 동작을 audit_log 테이블에 INSERT하여 감사 이력을 남김.

    action_type에 따른 기록 예시:
        - "query"          : 사용자 질의 접수 시 query_text 기록
        - "intent_classify" : 의도 분류 결과를 metadata에 기록
        - "sql_generate"   : LLM이 생성한 SQL을 sql_generated에 기록
        - "sql_execute"    : SQL 실행 결과를 result_summary에 기록
        - "doc_search"     : 문서 검색 결과를 result_summary에 기록
        - "error"          : 오류 발생 시 status='failed', result_summary에 오류 내용 기록

    Args:
        action_type: 동작 유형 문자열 ('query', 'sql_generate', 'execution', 'error' 등).
        query_text: 사용자의 원본 자연어 질의. 질의 접수 단계에서 기록됩니다.
        sql_generated: LLM이 생성한 SQL 문. SQL 생성 단계에서 기록됩니다.
        result_summary: 실행 결과 요약 텍스트. 최종 응답 또는 오류 메시지를 담습니다.
        status: 처리 상태 ('success', 'failed', 'pending'). 기본값 'success'.
        user_id: 요청한 사용자 식별자. 기본값 'system' (단일 사용자 MVP).
        metadata: 추가 컨텍스트를 담는 딕셔너리. JSONB 컬럼에 저장되며,
                  의도 분류 결과, 소요 시간, 검색 결과 수 등을 포함할 수 있습니다.

    Note:
        이 함수는 예외를 전파하지 않습니다. audit_log INSERT가 실패해도
        logger.exception()으로만 기록하고, 에이전트의 주요 흐름에는 영향을 주지 않습니다.
        이는 감사 로그의 실패가 사용자 응답 품질을 저해하지 않도록 하기 위함입니다.
    """
    try:
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log
                    (action_type, user_id, query_text, sql_generated,
                     result_summary, status, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    action_type,
                    user_id,
                    query_text,
                    sql_generated,
                    result_summary,
                    status,
                    metadata_json,
                ),
            )

        logger.debug(f"Audit log recorded: action={action_type}, status={status}")

    except Exception:
        logger.exception(
            f"Failed to record audit log: action={action_type}, status={status}"
        )
