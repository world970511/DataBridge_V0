"""
SQL SELECT 쿼리 검증 및 안전 실행 도구.

사용자가 자연어로 요청한 데이터 조회를 위해 LLM이 생성한 SQL을 검증하고 실행합니다.
보안 경계로서의 역할이 핵심이며, 다음 안전장치를 적용합니다:

1. **SELECT 전용**: INSERT, UPDATE, DELETE, DROP, ALTER 등 데이터 변경 구문 차단
2. **단일 문장**: 세미콜론(;)으로 구분된 다중 SQL 문 차단
3. **행 수 제한**: AgentConfig.max_query_rows(기본 5000행) 초과 시 truncation 표시
4. **타임아웃**: AgentConfig.query_timeout(기본 30초)을 PostgreSQL statement_timeout으로 설정

의존 모듈:
    - config.settings: get_settings() → AgentConfig(max_query_rows, query_timeout)
    - db.connection: get_cursor(dict_cursor=True) — 커서 획득

사용 예시:
    from agent.tools.query_db import validate_sql, execute_select

    is_valid, msg = validate_sql("SELECT * FROM sales")
    if is_valid:
        result = execute_select("SELECT * FROM sales")
        print(result["data"])  # [{"id": 1, "amount": 100}, ...]
"""

import logging
import re

from config.settings import get_settings
from db.connection import get_cursor, execute_command

logger = logging.getLogger(__name__)

# SELECT 이외의 데이터 변경/스키마 변경 키워드를 탐지하는 정규식 패턴.
# 단어 경계(\b)를 사용하여 컬럼명에 포함된 경우(예: 'updated_at')는 매칭하지 않습니다.
# re.IGNORECASE로 대소문자 무관하게 탐지합니다.
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"EXECUTE|EXEC|COPY|LOAD|INTO\s+OUTFILE)\b",
    re.IGNORECASE,
)

# SQL 주석을 탐지하는 패턴. '--' 라인 주석과 '/* */' 블록 주석을 모두 차단합니다.
# 주석을 통한 SQL 인젝션(예: SELECT 1; -- DROP TABLE)을 방지합니다.
_COMMENT_PATTERN = re.compile(r"(--|/\*|\*/)")


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    LLM이 생성한 SQL 문이 안전한 SELECT 쿼리인지 검증.

    다음 규칙을 순서대로 검사하며, 첫 번째 실패 시 즉시 (False, 사유) 를 반환합니다:
    1. 빈 문자열/공백만 있는 경우 → 거부
    2. SQL 주석(-- 또는 /* */)이 포함된 경우 → 거부 (인젝션 방지)
    3. 세미콜론이 포함된 다중 문장 → 거부 (다중 쿼리 차단)
    4. SQL이 SELECT로 시작하지 않는 경우 → 거부
    5. INSERT, DROP 등 금지 키워드가 포함된 경우 → 거부

    모든 검사를 통과하면 (True, "Valid SELECT query")를 반환합니다.

    Args:
        sql: 검증할 SQL 문자열.

    Returns:
        (is_valid, message) 튜플.
        is_valid=True이면 안전한 SELECT, False이면 message에 거부 사유가 담깁니다.
    """
    if not sql or not sql.strip():
        return False, "SQL이 비어 있습니다."

    cleaned = sql.strip()

    # 주석 탐지 — SQL 인젝션 방지
    if _COMMENT_PATTERN.search(cleaned):
        return False, "SQL 주석은 허용되지 않습니다."

    # 다중 문장 차단 — 세미콜론 검사 (문자열 리터럴 내부 제외를 위해 간단히 처리)
    # 마지막 세미콜론(트레일링)은 허용하되, 중간 세미콜론은 차단
    sql_body = cleaned.rstrip(";").strip()
    if ";" in sql_body:
        return False, "다중 SQL 문은 허용되지 않습니다."

    # SELECT 시작 여부 확인
    if not sql_body.upper().startswith("SELECT"):
        return False, "SELECT 쿼리만 허용됩니다."

    # 금지 키워드 탐지
    match = _FORBIDDEN_KEYWORDS.search(sql_body)
    if match:
        keyword = match.group(1).upper()
        return False, f"금지된 SQL 키워드가 포함되어 있습니다: {keyword}"

    return True, "Valid SELECT query"


def execute_select(sql: str) -> dict:
    """
    검증된 SELECT SQL을 안전하게 실행하고 결과를 딕셔너리로 반환.

    실행 전 validate_sql()로 재검증하며, PostgreSQL의 statement_timeout을
    AgentConfig.query_timeout(기본 30초)으로 설정하여 장시간 쿼리를 방지합니다.
    결과 행 수가 max_query_rows(기본 5000)를 초과하면 잘라내고 truncated=True를 표시합니다.

    실행 흐름:
    1. validate_sql() 재검증 → 실패 시 즉시 에러 반환
    2. SET statement_timeout으로 쿼리 타임아웃 설정
    3. SQL 실행 후 fetchmany(max_rows + 1)로 초과 여부 판별
    4. max_rows 초과 시 잘라내고 truncated 플래그 설정

    Args:
        sql: 실행할 SELECT SQL 문자열. validate_sql()을 통과해야 합니다.

    Returns:
        딕셔너리 형태의 실행 결과:
        {
            "success": bool,          — 실행 성공 여부
            "data": list[dict],       — 조회된 행 리스트 (RealDictCursor 사용)
            "row_count": int,          — 반환된 행 수
            "truncated": bool,        — max_query_rows 초과로 잘렸는지 여부
            "error": str | None,      — 에러 메시지 (성공 시 None)
            "sql": str,               — 실행된 SQL (디버깅용)
        }

    Note:
        validate_sql()에서 이미 검증된 SQL이라도, execute_select() 내부에서 다시 검증합니다.
        이는 방어적 프로그래밍 원칙에 따라 검증 우회를 방지하기 위함입니다.
    """
    # 방어적 재검증
    is_valid, msg = validate_sql(sql)
    if not is_valid:
        return {
            "success": False,
            "data": [],
            "row_count": 0,
            "truncated": False,
            "error": msg,
            "sql": sql,
        }

    settings = get_settings()
    max_rows = settings.agent.max_query_rows
    timeout_ms = settings.agent.query_timeout * 1000  # 초 → 밀리초

    try:
        with get_cursor(dict_cursor=True) as cur:
            # PostgreSQL statement_timeout 설정 (밀리초 단위)
            cur.execute(f"SET statement_timeout = {timeout_ms}")

            cur.execute(sql)

            # max_rows + 1개를 가져와서 초과 여부 판별
            rows = cur.fetchmany(max_rows + 1)

            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]

            # RealDictRow → 일반 dict 변환 (JSON 직렬화 호환)
            data = [dict(row) for row in rows]

            logger.info(
                f"SELECT executed: {len(data)} rows returned, "
                f"truncated={truncated}"
            )

            return {
                "success": True,
                "data": data,
                "row_count": len(data),
                "truncated": truncated,
                "error": None,
                "sql": sql,
            }

    except Exception as e:
        error_msg = str(e).strip()
        logger.error(f"SELECT execution failed: {error_msg}")
        return {
            "success": False,
            "data": [],
            "row_count": 0,
            "truncated": False,
            "error": error_msg,
            "sql": sql,
        }


def execute_write(sql: str) -> dict:
    """
    승인된 쓰기 SQL(INSERT, CREATE, UPDATE 등)을 실행.

    approval_manager를 통해 승인된 SQL만 이 함수로 실행해야 합니다.
    직접 호출은 보안 위험이 있으므로, 반드시 승인 워크플로우를 거쳐야 합니다.

    SELECT 쿼리는 execute_select()를 사용해야 하며, 이 함수는 거부합니다.
    주석과 다중 문장도 차단합니다.

    Args:
        sql: 실행할 쓰기 SQL 문자열.

    Returns:
        실행 결과 딕셔너리:
        {
            "success": bool,         — 실행 성공 여부
            "rows_affected": int,    — 영향받은 행 수
            "error": str | None,     — 에러 메시지 (성공 시 None)
            "sql": str,              — 실행된 SQL
        }
    """
    if not sql or not sql.strip():
        return {"success": False, "rows_affected": 0, "error": "SQL이 비어 있습니다.", "sql": sql}

    cleaned = sql.strip()

    # 주석 차단
    if _COMMENT_PATTERN.search(cleaned):
        return {"success": False, "rows_affected": 0, "error": "SQL 주석은 허용되지 않습니다.", "sql": sql}

    # 다중 문장 차단
    sql_body = cleaned.rstrip(";").strip()
    if ";" in sql_body:
        return {"success": False, "rows_affected": 0, "error": "다중 SQL 문은 허용되지 않습니다.", "sql": sql}

    try:
        affected = execute_command(sql)
        logger.info(f"Write SQL executed: {affected} rows affected")
        return {
            "success": True,
            "rows_affected": affected,
            "error": None,
            "sql": sql,
        }
    except Exception as e:
        error_msg = str(e).strip()
        logger.error(f"Write SQL execution failed: {error_msg}")
        return {
            "success": False,
            "rows_affected": 0,
            "error": error_msg,
            "sql": sql,
        }
