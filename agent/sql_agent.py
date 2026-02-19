"""
SQL 에이전트 모듈 — 자연어 질의를 SQL로 변환하여 실행하고 결과를 요약.

사용자의 데이터 관련 요청을 처리하는 핵심 에이전트입니다. 다음 파이프라인으로 동작합니다:

1. **스키마 로드**: list_tables.get_all_tables_summary()로 카탈로그 스키마를 조회하여
   LLM이 올바른 테이블·컬럼명으로 SQL을 생성할 수 있도록 시스템 프롬프트에 삽입합니다.
2. **SQL 생성**: Ollama LLM에 자연어 질의와 스키마를 전달하여 SQL을 생성합니다.
3. **코드블록 추출**: LLM 응답에서 ```sql ... ``` 코드블록 또는 순수 SQL 텍스트를 추출합니다.
4. **SQL 4단계 분류**: classify_sql()로 SAFE/AUTO_ALLOWED/NEEDS_APPROVAL/FORBIDDEN 분류
5. **분류별 처리**:
   - SAFE (SELECT): 즉시 실행
   - AUTO_ALLOWED (CREATE/INSERT/UPDATE): 즉시 실행
   - NEEDS_APPROVAL (DROP/DELETE/TRUNCATE/ALTER): 승인 요청 생성
   - FORBIDDEN: 차단
6. **결과 요약**: 실행 결과를 LLM에 전달하여 사용자 친화적 자연어 요약을 생성합니다.

모든 단계에서 _audit.log_action()으로 감사 로그를 기록합니다.

의존 모듈:
    - agent._llm: generate() — Ollama LLM 호출
    - agent._audit: log_action() — 감사 로그 기록
    - agent.tools.query_db: validate_sql(), execute_select(), execute_write() — SQL 실행
    - agent.tools.list_tables: get_all_tables_summary() — 스키마 정보
    - approval.sql_classifier: classify_sql(), SqlCategory — SQL 위험도 분류
    - approval.approval_manager: create_request() — 승인 요청 생성

사용 예시:
    from agent.sql_agent import process
    result = process("sales 테이블에서 총 매출 보여줘", user_id="kim")
    print(result["answer"])   # "총 매출은 1,234,000원입니다."
    print(result["sql"])      # "SELECT SUM(amount) AS total FROM sales"
"""

import logging
import re

from agent._llm import generate
from agent._audit import log_action
from agent.tools.query_db import validate_sql, execute_select, execute_write
from agent.tools.list_tables import get_all_tables_summary
from approval.sql_classifier import classify_sql, SqlCategory
from approval.approval_manager import create_request

logger = logging.getLogger(__name__)

# SQL 에이전트의 시스템 프롬프트 템플릿.
# SELECT뿐 아니라 CREATE, INSERT, DELETE 등도 허용하되,
# 실제 실행 여부는 sql_classifier에 의해 결정됩니다.
_SQL_SYSTEM_PROMPT = """당신은 PostgreSQL SQL 전문가입니다.
사용자의 자연어 질문을 정확한 SQL로 변환합니다.

규칙:
- 사용자가 데이터 조회를 요청하면 SELECT 쿼리를 작성합니다
- 사용자가 데이터 삭제/수정/생성을 요청하면 해당하는 SQL(DELETE, UPDATE, CREATE, INSERT 등)을 작성합니다
- SQL은 반드시 ```sql 코드블록으로 감싸서 응답합니다
- 테이블명과 컬럼명은 아래 스키마 정보를 정확히 참조합니다
- 쿼리 문 작성 시 주석을 달지 않습니다.
- 한국어 질문에 대해 한국어 별칭(alias)을 적절히 사용합니다
- 집계 함수 사용 시 적절한 GROUP BY를 포함합니다
- 결과가 너무 많을 수 있으면 LIMIT을 추가합니다

{schema}
"""

# SQL 실행 결과를 요약하기 위한 시스템 프롬프트.
_SUMMARY_SYSTEM_PROMPT = """당신은 데이터 분석 결과를 설명하는 전문가입니다.
SQL 쿼리 실행 결과를 사용자가 이해하기 쉬운 한국어로 요약합니다.

규칙:
- 핵심 수치와 인사이트를 간결하게 전달합니다
- 행 수가 많으면 주요 패턴이나 상위/하위 항목을 강조합니다
- 전문 용어보다 일상적인 표현을 사용합니다
"""


def process(question: str, user_id: str = "system") -> dict:
    """
    자연어 질의를 SQL로 변환하여 4단계 분류 후 실행/승인요청하는 전체 파이프라인.

    처리 흐름:
    1. 사용자 질의를 audit_log에 기록 (action_type='query')
    2. 카탈로그에서 테이블 스키마 정보를 로드하여 시스템 프롬프트 구성
    3. LLM에 자연어→SQL 변환 요청 → ```sql 코드블록에서 SQL 추출
    4. 추출된 SQL을 classify_sql()로 4단계 분류
    5. 분류에 따른 처리:
       - SAFE: validate_sql() → execute_select() → 결과 요약
       - AUTO_ALLOWED: execute_write() → 결과 메시지
       - NEEDS_APPROVAL: create_request() → 승인 대기 메시지
       - FORBIDDEN: 차단 메시지
    6. 각 단계의 결과를 audit_log에 기록

    Args:
        question: 사용자의 자연어 데이터 관련 질의.
        user_id: 요청한 사용자의 username. 기본값 'system'.

    Returns:
        처리 결과 딕셔너리:
        {
            "success": bool,            — 전체 파이프라인 성공 여부
            "answer": str,              — 사용자에게 표시할 자연어 응답
            "sql": str | None,          — LLM이 생성한 SQL
            "data": list[dict],         — 쿼리 실행 결과 행 리스트 (SELECT 시)
            "row_count": int,           — 반환된 행 수
            "truncated": bool,          — max_query_rows 초과로 잘렸는지 여부
            "agent": "sql",             — 처리한 에이전트 식별자
            "sql_category": str | None, — SQL 위험도 분류
            "approval_id": int | None,  — 승인 요청 ID (NEEDS_APPROVAL 시)
        }
    """
    # 1. 질의 접수 로그
    log_action(action_type="query", query_text=question, user_id=user_id)

    # 2. 스키마 로드
    schema = get_all_tables_summary()
    if schema == "등록된 테이블이 없습니다.":
        return _error_result(
            "데이터베이스에 등록된 테이블이 없습니다. 먼저 데이터 파일을 업로드해 주세요.",
            question,
            user_id=user_id,
        )

    # 3. LLM으로 SQL 생성 (에이전트용 모델 사용 - 데이터 보안)
    system_prompt = _SQL_SYSTEM_PROMPT.format(schema=schema)
    llm_response = generate(
        prompt=f"다음 질문을 SQL로 변환해 주세요:\n\n{question}",
        system=system_prompt,
        purpose="agent",  # 에이전트용 모델 (로컬 모델 권장 - 스키마 정보 보호)
        temperature=0.1,
    )

    if not llm_response:
        return _error_result(
            "LLM 서버에 연결할 수 없습니다. Ollama 서비스 상태를 확인해 주세요.",
            question,
            user_id=user_id,
        )

    # 4. 응답에서 SQL 추출
    sql = _extract_sql(llm_response)
    if not sql:
        log_action(
            action_type="sql_generate",
            query_text=question,
            result_summary=f"SQL 추출 실패. LLM 응답: {llm_response[:200]}",
            status="failed",
            user_id=user_id,
        )
        return _error_result(
            "SQL을 생성하지 못했습니다. 질문을 더 구체적으로 작성해 주세요.",
            question,
            user_id=user_id,
        )

    log_action(
        action_type="sql_generate",
        query_text=question,
        sql_generated=sql,
        status="success",
        user_id=user_id,
    )

    # 5. SQL 4단계 분류
    category, reason = classify_sql(sql)

    log_action(
        action_type="sql_classify",
        query_text=question,
        sql_generated=sql,
        result_summary=reason,
        status="success",
        user_id=user_id,
        metadata={"sql_category": category.value},
    )

    # 6. 분류에 따른 처리
    if category == SqlCategory.SAFE:
        return _handle_safe(question, sql, user_id)
    elif category == SqlCategory.AUTO_ALLOWED:
        return _handle_auto_allowed(question, sql, user_id)
    elif category == SqlCategory.NEEDS_APPROVAL:
        return _handle_needs_approval(question, sql, user_id, reason)
    else:  # FORBIDDEN
        return _handle_forbidden(question, sql, user_id, reason)


def _handle_safe(question: str, sql: str, user_id: str) -> dict:
    """
    SAFE (SELECT) SQL 처리 — 기존 파이프라인과 동일.

    validate_sql()로 재검증 후 execute_select()로 실행하고 결과를 요약합니다.
    """
    # SELECT 검증
    is_valid, validation_msg = validate_sql(sql)
    if not is_valid:
        log_action(
            action_type="sql_validate",
            query_text=question,
            sql_generated=sql,
            result_summary=validation_msg,
            status="failed",
            user_id=user_id,
        )
        return _error_result(
            f"생성된 SQL이 보안 검증을 통과하지 못했습니다: {validation_msg}",
            question,
            sql=sql,
            user_id=user_id,
        )

    # SQL 실행
    exec_result = execute_select(sql)

    if not exec_result["success"]:
        log_action(
            action_type="sql_execute",
            query_text=question,
            sql_generated=sql,
            result_summary=exec_result["error"],
            status="failed",
            user_id=user_id,
        )
        return _error_result(
            f"SQL 실행 중 오류가 발생했습니다: {exec_result['error']}",
            question,
            sql=sql,
            user_id=user_id,
        )

    # 결과 요약 생성
    answer = _summarize_results(question, sql, exec_result)

    log_action(
        action_type="sql_execute",
        query_text=question,
        sql_generated=sql,
        result_summary=answer[:500],
        status="success",
        user_id=user_id,
        metadata={
            "row_count": exec_result["row_count"],
            "truncated": exec_result["truncated"],
        },
    )

    return {
        "success": True,
        "answer": answer,
        "sql": sql,
        "data": exec_result["data"],
        "row_count": exec_result["row_count"],
        "truncated": exec_result["truncated"],
        "agent": "sql",
        "sql_category": SqlCategory.SAFE.value,
        "approval_id": None,
    }


def _handle_auto_allowed(question: str, sql: str, user_id: str) -> dict:
    """
    AUTO_ALLOWED (CREATE/INSERT/UPDATE) SQL 처리 — 승인 없이 즉시 실행.
    """
    exec_result = execute_write(sql)

    if not exec_result["success"]:
        log_action(
            action_type="sql_execute",
            query_text=question,
            sql_generated=sql,
            result_summary=exec_result["error"],
            status="failed",
            user_id=user_id,
        )
        return _error_result(
            f"SQL 실행 중 오류가 발생했습니다: {exec_result['error']}",
            question,
            sql=sql,
            user_id=user_id,
        )

    answer = f"✅ SQL이 성공적으로 실행되었습니다. ({exec_result['rows_affected']}행 영향받음)"

    log_action(
        action_type="sql_execute",
        query_text=question,
        sql_generated=sql,
        result_summary=answer,
        status="success",
        user_id=user_id,
        metadata={"rows_affected": exec_result["rows_affected"]},
    )

    return {
        "success": True,
        "answer": answer,
        "sql": sql,
        "data": [],
        "row_count": 0,
        "truncated": False,
        "agent": "sql",
        "sql_category": SqlCategory.AUTO_ALLOWED.value,
        "approval_id": None,
    }


def _handle_needs_approval(
    question: str, sql: str, user_id: str, reason: str
) -> dict:
    """
    NEEDS_APPROVAL (DROP/DELETE/TRUNCATE/ALTER) SQL 처리 — 승인 요청 생성.

    실행하지 않고 approval_requests 테이블에 요청을 저장합니다.
    관리자가 승인 후 execute_approved()로 실행합니다.
    """
    req_id = create_request(
        sql=sql,
        title=question[:200],
        requested_by=user_id,
        sql_category=SqlCategory.NEEDS_APPROVAL.value,
        metadata={"original_question": question},
    )

    if req_id:
        answer = (
            f"🔒 이 SQL은 관리자 승인이 필요합니다.\n\n"
            f"**사유**: {reason}\n"
            f"**요청 ID**: {req_id}\n"
            f"**SQL**:\n```sql\n{sql}\n```\n\n"
            f"관리자가 '승인 관리' 페이지에서 승인하면 실행됩니다."
        )
    else:
        answer = "승인 요청을 생성하지 못했습니다. 시스템 관리자에게 문의해 주세요."

    return {
        "success": True,  # 요청 생성 자체는 성공
        "answer": answer,
        "sql": sql,
        "data": [],
        "row_count": 0,
        "truncated": False,
        "agent": "sql",
        "sql_category": SqlCategory.NEEDS_APPROVAL.value,
        "approval_id": req_id,
    }


def _handle_forbidden(question: str, sql: str, user_id: str, reason: str) -> dict:
    """
    FORBIDDEN SQL 처리 — 항상 차단.
    """
    log_action(
        action_type="sql_blocked",
        query_text=question,
        sql_generated=sql,
        result_summary=reason,
        status="failed",
        user_id=user_id,
    )

    return _error_result(
        f"⛔ 이 SQL은 보안 정책에 의해 차단되었습니다: {reason}",
        question,
        sql=sql,
        user_id=user_id,
        sql_category=SqlCategory.FORBIDDEN.value,
    )


def _extract_sql(llm_response: str) -> str:
    """
    LLM 응답에서 SQL 쿼리를 추출.

    추출 우선순위:
    1. ```sql ... ``` 마크다운 코드블록에서 SQL 추출
    2. ``` ... ``` 일반 코드블록에서 SQL 추출
    3. 응답 전체에서 SQL 키워드로 시작하는 줄을 찾아 추출

    Args:
        llm_response: LLM이 반환한 전체 응답 텍스트.

    Returns:
        추출된 SQL 문자열 (앞뒤 공백 제거).
        추출 실패 시 빈 문자열("").
    """
    # 1. ```sql ... ``` 코드블록
    match = re.search(r"```sql\s*\n?(.*?)```", llm_response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 2. ``` ... ``` 일반 코드블록
    match = re.search(r"```\s*\n?(.*?)```", llm_response, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if _is_sql_statement(candidate):
            return candidate

    # 3. SQL 키워드로 시작하는 텍스트 탐색
    _SQL_START_KEYWORDS = ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE")
    for line in llm_response.split("\n"):
        stripped = line.strip()
        if any(stripped.upper().startswith(kw) for kw in _SQL_START_KEYWORDS):
            remaining = llm_response[llm_response.index(stripped):]
            sql_lines = []
            for sql_line in remaining.split("\n"):
                if sql_line.strip() == "" and sql_lines:
                    break
                if sql_line.strip().startswith("#") or sql_line.strip().startswith("*"):
                    break
                sql_lines.append(sql_line)
            if sql_lines:
                return "\n".join(sql_lines).strip().rstrip(";") + ";"

    return ""


def _is_sql_statement(text: str) -> bool:
    """텍스트가 SQL 문으로 시작하는지 확인하는 헬퍼."""
    _SQL_START_KEYWORDS = ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE")
    return any(text.upper().startswith(kw) for kw in _SQL_START_KEYWORDS)


def _summarize_results(question: str, sql: str, exec_result: dict) -> str:
    """
    SQL 실행 결과를 LLM으로 사용자 친화적 자연어 요약으로 변환.

    데이터가 비어 있으면 LLM 호출 없이 "결과 없음" 메시지를 반환합니다.
    데이터가 있으면 상위 20행까지만 LLM에 전달하여 요약을 요청합니다.
    LLM 요약 실패 시 행 수와 잘림 여부만 포함한 기본 메시지를 반환합니다.

    Args:
        question: 사용자의 원본 질의.
        sql: 실행된 SQL 문.
        exec_result: execute_select()의 반환 딕셔너리.

    Returns:
        사용자에게 표시할 자연어 응답 문자열.
    """
    data = exec_result["data"]
    row_count = exec_result["row_count"]
    truncated = exec_result["truncated"]

    if not data:
        return "조건에 맞는 데이터가 없습니다."

    # 요약을 위해 상위 20행만 LLM에 전달 (토큰 절약)
    preview_data = data[:20]
    data_text = "\n".join([str(row) for row in preview_data])

    truncation_note = ""
    if truncated:
        truncation_note = f"\n(참고: 전체 결과가 {row_count}행 이상이며, 일부만 표시되었습니다)"
    elif row_count > 20:
        truncation_note = f"\n(참고: 전체 {row_count}행 중 상위 20행의 데이터입니다)"

    summary_prompt = (
        f"사용자 질문: {question}\n"
        f"실행된 SQL: {sql}\n"
        f"결과 ({row_count}행):\n{data_text}{truncation_note}\n\n"
        "위 데이터를 바탕으로 사용자 질문에 대한 답변을 작성해 주세요."
    )

    summary = generate(
        prompt=summary_prompt,
        system=_SUMMARY_SYSTEM_PROMPT,
        purpose="agent",  # 에이전트용 모델 (쿼리 결과 데이터 포함)
        temperature=0.3,
    )

    if summary:
        return summary

    # LLM 요약 실패 시 기본 메시지
    fallback = f"쿼리 결과: {row_count}행이 조회되었습니다."
    if truncated:
        fallback += " (결과가 너무 많아 일부만 표시됩니다)"
    return fallback


def _error_result(
    message: str,
    question: str,
    sql: str = None,
    user_id: str = "system",
    sql_category: str = None,
) -> dict:
    """
    에러 발생 시 반환할 표준 결과 딕셔너리를 생성.

    파이프라인의 어느 단계에서든 실패 시 일관된 형식의 에러 응답을 만들기 위한
    내부 헬퍼 함수입니다. 에러 내용은 audit_log에도 기록됩니다.

    Args:
        message: 사용자에게 표시할 에러 메시지.
        question: 사용자의 원본 질의 (로그용).
        sql: 생성된 SQL (있는 경우).
        user_id: 요청한 사용자 ID.
        sql_category: SQL 위험도 분류 (있는 경우).

    Returns:
        success=False인 표준 결과 딕셔너리.
    """
    log_action(
        action_type="error",
        query_text=question,
        sql_generated=sql,
        result_summary=message,
        status="failed",
        user_id=user_id,
    )

    return {
        "success": False,
        "answer": message,
        "sql": sql,
        "data": [],
        "row_count": 0,
        "truncated": False,
        "agent": "sql",
        "sql_category": sql_category,
        "approval_id": None,
    }
