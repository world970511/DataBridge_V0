"""
데이터 마트 빌더 도구.

사용자의 자연어 요청을 기반으로 기존 테이블에서 집계·가공된 데이터 마트를
CREATE TABLE AS SELECT 방식으로 생성합니다.

마트 명명 규칙:
    - Settings.agent.mart_prefix (기본 "mart_")로 시작하는 테이블명 사용
    - 예: mart_daily_sales, mart_customer_summary

마트 생성 파이프라인:
    1. 스키마 로드: 카탈로그에서 기존 테이블 구조 조회
    2. SQL 생성: LLM이 CREATE TABLE AS SELECT SQL 생성
    3. SQL 검증: 마트용 SQL 검증 (CREATE TABLE ... AS SELECT만 허용)
    4. 실행: execute_write()로 즉시 실행 (AUTO_ALLOWED)
    5. 카탈로그 등록: 생성된 마트 테이블을 카탈로그에 자동 등록
    6. 결과 반환: 마트 이름, 행 수, 컬럼 정보 등

의존 모듈:
    - agent._llm: generate() — LLM 호출
    - agent._audit: log_action() — 감사 로그
    - agent.tools.query_db: execute_write(), execute_select() — SQL 실행
    - agent.tools.list_tables: get_all_tables_summary() — 스키마 정보
    - catalog.catalog: register_table() — 카탈로그 등록
    - config.settings: get_settings() — mart_prefix 등 설정

사용 예시:
    from agent.tools.create_mart import create_mart

    result = create_mart(
        question="월별 제품별 매출 합계 마트를 만들어줘",
        user_id="kim",
    )
    print(result["mart_name"])   # "mart_monthly_product_sales"
    print(result["row_count"])   # 120
"""

import json
import logging
import re
from typing import Optional

from agent._llm import generate
from agent._audit import log_action
from agent.tools.query_db import execute_write, execute_select
from agent.tools.list_tables import get_all_tables_summary
from config.settings import get_settings

logger = logging.getLogger(__name__)

# 마트 생성용 시스템 프롬프트.
_MART_SYSTEM_PROMPT = """You are a PostgreSQL data mart architect.
Your task is to create a data mart table from existing tables based on the user's request.

Rules:
- Generate a single CREATE TABLE ... AS SELECT statement
- The mart table name MUST start with "{prefix}" (e.g., {prefix}monthly_sales)
- Choose a descriptive mart name that reflects its purpose
- Use appropriate aggregations (SUM, AVG, COUNT, etc.) as needed
- Include proper GROUP BY when using aggregate functions
- Add meaningful column aliases
- Do NOT use comments in SQL
- Wrap the SQL in ```sql code blocks

{schema}
"""

# 마트 SQL에서 테이블명을 추출하는 정규식
_MART_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?",
    re.IGNORECASE,
)


def create_mart(
    question: str,
    user_id: str = "system",
    mart_name: Optional[str] = None,
) -> dict:
    """
    자연어 요청으로 데이터 마트를 생성.

    처리 흐름:
    1. 카탈로그에서 기존 테이블 스키마 로드
    2. LLM으로 CREATE TABLE AS SELECT SQL 생성
    3. SQL 코드블록에서 추출 및 검증
    4. execute_write()로 실행
    5. 생성된 마트를 카탈로그에 등록
    6. 결과 반환

    Args:
        question: 사용자의 마트 생성 요청 (자연어).
        user_id: 요청한 사용자 username.
        mart_name: 마트 테이블명을 직접 지정할 경우. None이면 LLM이 결정.

    Returns:
        마트 생성 결과 딕셔너리:
        {
            "success": bool,
            "answer": str,           — 사용자에게 표시할 응답
            "mart_name": str | None, — 생성된 마트 테이블명
            "sql": str | None,       — 실행된 SQL
            "row_count": int,        — 마트 행 수
            "column_count": int,     — 마트 컬럼 수
            "agent": "mart_builder",
        }
    """
    settings = get_settings()
    prefix = settings.agent.mart_prefix

    log_action(
        action_type="mart_create_request",
        query_text=question,
        user_id=user_id,
    )

    # 1. 스키마 로드
    schema = get_all_tables_summary()
    if schema == "No tables registered.":
        return _mart_error(
            "등록된 테이블이 없습니다. 먼저 데이터를 업로드해 주세요.",
            question, user_id,
        )

    # 2. LLM으로 SQL 생성
    system_prompt = _MART_SYSTEM_PROMPT.format(prefix=prefix, schema=schema)

    user_prompt = f"Create a data mart based on this request:\n\n{question}"
    if mart_name:
        user_prompt += f"\n\nUse this exact mart table name: {mart_name}"

    llm_response = generate(
        prompt=user_prompt,
        system=system_prompt,
        purpose="agent",
        temperature=0.1,
    )

    if not llm_response:
        return _mart_error(
            "LLM 서버에 연결할 수 없습니다.",
            question, user_id,
        )

    # 3. SQL 추출 및 검증
    sql = _extract_mart_sql(llm_response)
    if not sql:
        log_action(
            action_type="mart_sql_generate",
            query_text=question,
            result_summary=f"SQL 추출 실패. LLM 응답: {llm_response[:300]}",
            status="failed",
            user_id=user_id,
        )
        return _mart_error(
            "마트 생성 SQL을 생성하지 못했습니다. 요청을 더 구체적으로 작성해 주세요.",
            question, user_id,
        )

    # 테이블명 추출
    extracted_name = _extract_mart_name(sql)
    if not extracted_name:
        return _mart_error(
            "마트 테이블명을 추출할 수 없습니다.",
            question, user_id, sql=sql,
        )

    # mart_ 접두어 확인
    if not extracted_name.lower().startswith(prefix.lower()):
        return _mart_error(
            f"마트 테이블명은 '{prefix}'로 시작해야 합니다. 생성된 이름: {extracted_name}",
            question, user_id, sql=sql,
        )

    # SQL 유형 검증 (CREATE TABLE ... AS SELECT만 허용)
    validation_error = _validate_mart_sql(sql)
    if validation_error:
        return _mart_error(validation_error, question, user_id, sql=sql)

    log_action(
        action_type="mart_sql_generate",
        query_text=question,
        sql_generated=sql,
        status="success",
        user_id=user_id,
        metadata={"mart_name": extracted_name},
    )

    # 4. SQL 실행
    exec_result = execute_write(sql)

    if not exec_result["success"]:
        log_action(
            action_type="mart_create",
            query_text=question,
            sql_generated=sql,
            result_summary=exec_result["error"],
            status="failed",
            user_id=user_id,
        )
        return _mart_error(
            f"마트 생성 중 오류가 발생했습니다: {exec_result['error']}",
            question, user_id, sql=sql,
        )

    # 5. 카탈로그 등록
    row_count, column_count, columns_json = _inspect_mart_table(extracted_name)

    try:
        from catalog.catalog import register_table
        register_table(
            table_name=extracted_name,
            source_file=f"mart:{user_id}:{question[:100]}",
            file_type="mart",
            row_count=row_count,
            column_count=column_count,
            columns_json=columns_json,
            description=f"[마트] {question[:200]}",
            data_category="statistics",
            tags=["mart", "자동생성"],
        )
    except Exception as e:
        logger.error(f"Failed to register mart in catalog: {e}")
        # 카탈로그 등록 실패해도 마트 자체는 생성되었으므로 계속 진행

    # 6. 결과 반환
    answer = (
        f"✅ 데이터 마트가 생성되었습니다.\n\n"
        f"**마트명**: `{extracted_name}`\n"
        f"**행 수**: {row_count:,}행\n"
        f"**컬럼 수**: {column_count}개\n"
        f"**컬럼**: {_format_columns_brief(columns_json)}\n\n"
        f"```sql\n{sql}\n```"
    )

    log_action(
        action_type="mart_create",
        query_text=question,
        sql_generated=sql,
        result_summary=answer[:500],
        status="success",
        user_id=user_id,
        metadata={
            "mart_name": extracted_name,
            "row_count": row_count,
            "column_count": column_count,
        },
    )

    from notifications.dispatcher import emit_event
    emit_event("mart.created", {"mart": extracted_name, "rows": row_count, "columns": column_count, "user": user_id})

    return {
        "success": True,
        "answer": answer,
        "mart_name": extracted_name,
        "sql": sql,
        "row_count": row_count,
        "column_count": column_count,
        "agent": "mart_builder",
    }


def list_marts() -> list[dict]:
    """
    mart_ 접두어가 붙은 마트 테이블 목록을 카탈로그에서 조회.

    Returns:
        마트 테이블 메타데이터 리스트.
    """
    settings = get_settings()
    prefix = settings.agent.mart_prefix

    try:
        from catalog.catalog import list_tables
        all_tables = list_tables()
        return [t for t in all_tables if t["table_name"].startswith(prefix)]
    except Exception as e:
        logger.error(f"Failed to list marts: {e}")
        return []


def refresh_mart(mart_name: str, user_id: str = "system") -> dict:
    """
    기존 마트를 DROP 후 재생성 (카탈로그의 원본 SQL 기반).

    현재는 마트 생성 SQL을 보관하지 않으므로 미구현.
    향후 batch_jobs와 연계하여 구현 예정.

    Args:
        mart_name: 갱신할 마트 테이블명.
        user_id: 요청자 username.

    Returns:
        결과 딕셔너리.
    """
    return {
        "success": False,
        "answer": "마트 갱신 기능은 아직 지원되지 않습니다. 배치 작업으로 등록하여 주기적으로 갱신할 수 있습니다.",
        "mart_name": mart_name,
        "sql": None,
        "row_count": 0,
        "column_count": 0,
        "agent": "mart_builder",
    }


# ── 내부 헬퍼 함수들 ──


def _extract_mart_sql(llm_response: str) -> str:
    """LLM 응답에서 CREATE TABLE SQL을 추출."""
    # 1. ```sql ... ``` 코드블록
    match = re.search(r"```sql\s*\n?(.*?)```", llm_response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 2. ``` ... ``` 일반 코드블록
    match = re.search(r"```\s*\n?(.*?)```", llm_response, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if candidate.upper().startswith("CREATE"):
            return candidate

    # 3. CREATE로 시작하는 텍스트 탐색
    for line in llm_response.split("\n"):
        stripped = line.strip()
        if stripped.upper().startswith("CREATE"):
            remaining = llm_response[llm_response.index(stripped):]
            sql_lines = []
            for sql_line in remaining.split("\n"):
                if sql_line.strip() == "" and sql_lines:
                    break
                if sql_line.strip().startswith("#") or sql_line.strip().startswith("*"):
                    break
                sql_lines.append(sql_line)
            if sql_lines:
                return "\n".join(sql_lines).strip()

    return ""


def _extract_mart_name(sql: str) -> str:
    """SQL에서 마트 테이블명을 추출."""
    match = _MART_TABLE_RE.search(sql)
    if match:
        return match.group(1)
    return ""


def _validate_mart_sql(sql: str) -> str:
    """
    마트 SQL을 검증. 오류가 있으면 메시지 반환, 없으면 빈 문자열.

    허용하는 패턴:
    - CREATE TABLE mart_xxx AS SELECT ...
    - CREATE TABLE IF NOT EXISTS mart_xxx AS SELECT ...

    금지:
    - 주석
    - 다중 문장
    - DROP, DELETE, TRUNCATE, ALTER, GRANT 등
    - AS SELECT가 없는 CREATE TABLE (직접 테이블 정의)
    """
    cleaned = sql.strip()

    # 주석 차단
    if re.search(r"(--|/\*|\*/)", cleaned):
        return "SQL 주석은 허용되지 않습니다."

    # 다중 문장 차단
    sql_body = cleaned.rstrip(";").strip()
    if ";" in sql_body:
        return "다중 SQL 문은 허용되지 않습니다."

    # CREATE TABLE ... AS SELECT 패턴 확인
    if not re.match(r"\s*CREATE\s+TABLE", sql_body, re.IGNORECASE):
        return "CREATE TABLE 문만 허용됩니다."

    if not re.search(r"\bAS\s+SELECT\b", sql_body, re.IGNORECASE):
        return "CREATE TABLE ... AS SELECT 형식만 허용됩니다."

    # 위험 키워드 차단
    forbidden = re.compile(
        r"\b(DROP|DELETE|TRUNCATE|ALTER|GRANT|REVOKE|EXECUTE|EXEC|COPY|LOAD)\b",
        re.IGNORECASE,
    )
    match = forbidden.search(sql_body)
    if match:
        return f"금지된 SQL 키워드가 포함되어 있습니다: {match.group(1).upper()}"

    return ""


def _inspect_mart_table(mart_name: str) -> tuple[int, int, list[dict]]:
    """
    생성된 마트 테이블의 행 수, 컬럼 수, 컬럼 정보를 조회.

    Returns:
        (row_count, column_count, columns_json)
    """
    row_count = 0
    column_count = 0
    columns_json = []

    try:
        # 행 수 조회
        count_result = execute_select(f'SELECT COUNT(*) AS cnt FROM "{mart_name}"')
        if count_result["success"] and count_result["data"]:
            row_count = count_result["data"][0].get("cnt", 0)
    except Exception as e:
        logger.warning(f"Failed to count rows in mart {mart_name}: {e}")

    try:
        # 컬럼 정보 조회 (information_schema)
        from db.connection import execute_query
        columns = execute_query(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
            """,
            (mart_name,),
        )
        columns_json = [
            {"name": c["column_name"], "type": c["data_type"]}
            for c in columns
        ]
        column_count = len(columns_json)
    except Exception as e:
        logger.warning(f"Failed to get columns for mart {mart_name}: {e}")

    return row_count, column_count, columns_json


def _format_columns_brief(columns_json: list[dict]) -> str:
    """컬럼 정보를 간략하게 포맷."""
    if not columns_json:
        return "(컬럼 정보 없음)"

    parts = [f"{c['name']}({c['type']})" for c in columns_json[:10]]
    text = ", ".join(parts)
    if len(columns_json) > 10:
        text += f" ... 외 {len(columns_json) - 10}개"
    return text


def _mart_error(
    message: str,
    question: str,
    user_id: str,
    sql: str = None,
) -> dict:
    """마트 생성 에러 결과 딕셔너리."""
    log_action(
        action_type="mart_error",
        query_text=question,
        sql_generated=sql,
        result_summary=message,
        status="failed",
        user_id=user_id,
    )
    return {
        "success": False,
        "answer": message,
        "mart_name": None,
        "sql": sql,
        "row_count": 0,
        "column_count": 0,
        "agent": "mart_builder",
    }
