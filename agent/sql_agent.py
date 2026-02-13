"""
SQL 에이전트 모듈 — 자연어 질의를 SQL로 변환하여 실행하고 결과를 요약.

사용자의 데이터 조회 요청을 처리하는 핵심 에이전트입니다. 다음 파이프라인으로 동작합니다:

1. **스키마 로드**: list_tables.get_all_tables_summary()로 카탈로그 스키마를 조회하여
   LLM이 올바른 테이블·컬럼명으로 SQL을 생성할 수 있도록 시스템 프롬프트에 삽입합니다.
2. **SQL 생성**: Ollama LLM에 자연어 질의와 스키마를 전달하여 SELECT SQL을 생성합니다.
3. **코드블록 추출**: LLM 응답에서 ```sql ... ``` 코드블록 또는 순수 SQL 텍스트를 추출합니다.
4. **SQL 검증**: query_db.validate_sql()로 안전한 SELECT인지 검증합니다.
5. **SQL 실행**: query_db.execute_select()로 PostgreSQL에서 실행합니다.
6. **결과 요약**: 실행 결과를 LLM에 전달하여 사용자 친화적 자연어 요약을 생성합니다.

모든 단계에서 _audit.log_action()으로 감사 로그를 기록합니다.

의존 모듈:
    - agent._llm: generate() — Ollama LLM 호출
    - agent._audit: log_action() — 감사 로그 기록
    - agent.tools.query_db: validate_sql(), execute_select() — SQL 검증·실행
    - agent.tools.list_tables: get_all_tables_summary() — 스키마 정보

사용 예시:
    from agent.sql_agent import process
    result = process("sales 테이블에서 총 매출 보여줘")
    print(result["answer"])   # "총 매출은 1,234,000원입니다."
    print(result["sql"])      # "SELECT SUM(amount) AS total FROM sales"
"""

import logging
import re

from agent._llm import generate
from agent._audit import log_action
from agent.tools.query_db import validate_sql, execute_select
from agent.tools.list_tables import get_all_tables_summary

logger = logging.getLogger(__name__)

# SQL 에이전트의 시스템 프롬프트 템플릿.
# {schema} 자리에 get_all_tables_summary()의 결과가 삽입됩니다.
_SQL_SYSTEM_PROMPT = """당신은 PostgreSQL SQL 전문가입니다.
사용자의 자연어 질문을 정확한 SELECT SQL로 변환합니다.

규칙:
- 반드시 SELECT 쿼리만 작성합니다 (INSERT, UPDATE, DELETE 금지)
- SQL은 반드시 ```sql 코드블록으로 감싸서 응답합니다
- 테이블명과 컬럼명은 아래 스키마 정보를 정확히 참조합니다
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


def process(question: str) -> dict:
    """
    자연어 질의를 SQL로 변환하여 실행하고 결과를 요약하는 전체 파이프라인을 수행.

    처리 흐름:
    1. 사용자 질의를 audit_log에 기록 (action_type='query')
    2. 카탈로그에서 테이블 스키마 정보를 로드하여 시스템 프롬프트 구성
    3. LLM에 자연어→SQL 변환 요청 → ```sql 코드블록에서 SQL 추출
    4. 추출된 SQL을 validate_sql()로 보안 검증
    5. execute_select()로 PostgreSQL에서 실행
    6. 실행 결과를 LLM에 전달하여 자연어 요약 생성
    7. 각 단계의 결과를 audit_log에 기록

    Args:
        question: 사용자의 자연어 데이터 조회 질의.
                  예: "지난달 매출 상위 10개 제품 보여줘"

    Returns:
        처리 결과 딕셔너리:
        {
            "success": bool,      — 전체 파이프라인 성공 여부
            "answer": str,        — 사용자에게 표시할 자연어 응답
            "sql": str | None,    — LLM이 생성한 SQL (생성 실패 시 None)
            "data": list[dict],   — 쿼리 실행 결과 행 리스트
            "row_count": int,     — 반환된 행 수
            "truncated": bool,    — max_query_rows 초과로 잘렸는지 여부
            "agent": "sql",       — 처리한 에이전트 식별자
        }

    Note:
        파이프라인 중간에 실패해도 예외를 전파하지 않고 success=False와 함께
        사용자 친화적 에러 메시지를 answer에 담아 반환합니다.
    """
    # 1. 질의 접수 로그
    log_action(action_type="query", query_text=question)

    # 2. 스키마 로드
    schema = get_all_tables_summary()
    if schema == "등록된 테이블이 없습니다.":
        return _error_result(
            "데이터베이스에 등록된 테이블이 없습니다. 먼저 데이터 파일을 업로드해 주세요.",
            question,
        )

    # 3. LLM으로 SQL 생성
    system_prompt = _SQL_SYSTEM_PROMPT.format(schema=schema)
    llm_response = generate(
        prompt=f"다음 질문을 SQL로 변환해 주세요:\n\n{question}",
        system=system_prompt,
        temperature=0.1,
    )

    if not llm_response:
        return _error_result(
            "LLM 서버에 연결할 수 없습니다. Ollama 서비스 상태를 확인해 주세요.",
            question,
        )

    # 4. 응답에서 SQL 추출
    sql = _extract_sql(llm_response)
    if not sql:
        log_action(
            action_type="sql_generate",
            query_text=question,
            result_summary=f"SQL 추출 실패. LLM 응답: {llm_response[:200]}",
            status="failed",
        )
        return _error_result(
            "SQL을 생성하지 못했습니다. 질문을 더 구체적으로 작성해 주세요.",
            question,
        )

    log_action(
        action_type="sql_generate",
        query_text=question,
        sql_generated=sql,
        status="success",
    )

    # 5. SQL 검증
    is_valid, validation_msg = validate_sql(sql)
    if not is_valid:
        log_action(
            action_type="sql_validate",
            query_text=question,
            sql_generated=sql,
            result_summary=validation_msg,
            status="failed",
        )
        return _error_result(
            f"생성된 SQL이 보안 검증을 통과하지 못했습니다: {validation_msg}",
            question,
            sql=sql,
        )

    # 6. SQL 실행
    exec_result = execute_select(sql)

    if not exec_result["success"]:
        log_action(
            action_type="sql_execute",
            query_text=question,
            sql_generated=sql,
            result_summary=exec_result["error"],
            status="failed",
        )
        return _error_result(
            f"SQL 실행 중 오류가 발생했습니다: {exec_result['error']}",
            question,
            sql=sql,
        )

    # 7. 결과 요약 생성
    answer = _summarize_results(question, sql, exec_result)

    log_action(
        action_type="sql_execute",
        query_text=question,
        sql_generated=sql,
        result_summary=answer[:500],
        status="success",
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
    }


def _extract_sql(llm_response: str) -> str:
    """
    LLM 응답에서 SQL 쿼리를 추출.

    추출 우선순위:
    1. ```sql ... ``` 마크다운 코드블록에서 SQL 추출
    2. ``` ... ``` 일반 코드블록에서 SQL 추출
    3. 응답 전체에서 SELECT로 시작하는 줄을 찾아 추출

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
        if candidate.upper().startswith("SELECT"):
            return candidate

    # 3. SELECT로 시작하는 텍스트 탐색
    for line in llm_response.split("\n"):
        stripped = line.strip()
        if stripped.upper().startswith("SELECT"):
            # SELECT부터 문장 끝까지 수집
            remaining = llm_response[llm_response.index(stripped):]
            # 다음 빈 줄이나 마크다운 구분까지
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
        temperature=0.3,
    )

    if summary:
        return summary

    # LLM 요약 실패 시 기본 메시지
    fallback = f"쿼리 결과: {row_count}행이 조회되었습니다."
    if truncated:
        fallback += " (결과가 너무 많아 일부만 표시됩니다)"
    return fallback


def _error_result(message: str, question: str, sql: str = None) -> dict:
    """
    에러 발생 시 반환할 표준 결과 딕셔너리를 생성.

    파이프라인의 어느 단계에서든 실패 시 일관된 형식의 에러 응답을 만들기 위한
    내부 헬퍼 함수입니다. 에러 내용은 audit_log에도 기록됩니다.

    Args:
        message: 사용자에게 표시할 에러 메시지.
        question: 사용자의 원본 질의 (로그용).
        sql: 생성된 SQL (있는 경우).

    Returns:
        success=False인 표준 결과 딕셔너리.
    """
    log_action(
        action_type="error",
        query_text=question,
        sql_generated=sql,
        result_summary=message,
        status="failed",
    )

    return {
        "success": False,
        "answer": message,
        "sql": sql,
        "data": [],
        "row_count": 0,
        "truncated": False,
        "agent": "sql",
    }
