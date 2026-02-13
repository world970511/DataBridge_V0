"""
SQL 문 4단계 위험도 분류 모듈.

LLM이 생성한 SQL 문의 첫 키워드를 분석하여 4단계 위험도로 분류합니다.
agent/tools/query_db.py의 validate_sql() 패턴을 참고하되, SELECT 이외의
SQL도 허용하는 확장된 분류 체계입니다.

4단계 분류:
    SAFE           : SELECT — 데이터 조회만 (읽기 전용)
    AUTO_ALLOWED   : CREATE TABLE, INSERT — 데이터 입력/테이블 생성 (승인 없이 실행)
    NEEDS_APPROVAL : DROP, DELETE, TRUNCATE, ALTER — 데이터 삭제/변경 (관리자 승인 필요)
    FORBIDDEN      : GRANT, REVOKE, EXECUTE, COPY, LOAD 등 — 보안 위험 (항상 차단)

의존 모듈: 없음 (표준 라이브러리만 사용)

사용 예시:
    from approval.sql_classifier import classify_sql, SqlCategory

    category, reason = classify_sql("SELECT * FROM sales")
    assert category == SqlCategory.SAFE

    category, reason = classify_sql("DROP TABLE sales")
    assert category == SqlCategory.NEEDS_APPROVAL
"""

import re
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class SqlCategory(str, Enum):
    """
    SQL 문의 4단계 위험도 카테고리.

    str을 상속하여 JSON 직렬화 및 문자열 비교가 가능합니다.

    Attributes:
        SAFE: 읽기 전용 SELECT. 즉시 실행 가능.
        AUTO_ALLOWED: CREATE TABLE, INSERT. 승인 없이 자동 실행 허용.
        NEEDS_APPROVAL: DROP, DELETE, TRUNCATE, ALTER. 관리자 승인 필요.
        FORBIDDEN: GRANT, REVOKE, EXECUTE, COPY 등. 항상 차단.
    """
    SAFE = "SAFE"
    AUTO_ALLOWED = "AUTO_ALLOWED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    FORBIDDEN = "FORBIDDEN"


# 각 카테고리에 매핑되는 SQL 키워드 패턴.
# 순서가 중요합니다 — FORBIDDEN을 먼저 검사하여 보안 위험을 우선 차단합니다.

# 항상 차단되는 SQL 키워드 (보안 위험)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(GRANT|REVOKE|EXECUTE|EXEC|COPY|LOAD|INTO\s+OUTFILE)\b",
    re.IGNORECASE,
)

# 관리자 승인이 필요한 SQL 키워드 (데이터 삭제/변경)
_NEEDS_APPROVAL_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER)\b",
    re.IGNORECASE,
)

# 승인 없이 자동 실행 가능한 SQL 키워드 (데이터 입력/구조 생성)
_AUTO_ALLOWED_KEYWORDS = re.compile(
    r"\b(CREATE|INSERT|UPDATE)\b",
    re.IGNORECASE,
)

# SQL 주석 탐지 패턴 — 주석이 포함된 SQL은 인젝션 위험으로 차단
_COMMENT_PATTERN = re.compile(r"(--|/\*|\*/)")


def classify_sql(sql: str) -> tuple[SqlCategory, str]:
    """
    SQL 문의 위험도를 4단계로 분류.

    분류 우선순위:
    1. 빈 SQL → FORBIDDEN ("SQL이 비어 있습니다")
    2. 주석 포함 → FORBIDDEN ("SQL 주석은 허용되지 않습니다")
    3. 다중 문장 (세미콜론) → FORBIDDEN ("다중 SQL 문은 허용되지 않습니다")
    4. FORBIDDEN 키워드 매칭 → FORBIDDEN
    5. NEEDS_APPROVAL 키워드 매칭 → NEEDS_APPROVAL
    6. SELECT로 시작 → SAFE
    7. AUTO_ALLOWED 키워드 매칭 → AUTO_ALLOWED
    8. 기타 → FORBIDDEN ("분류할 수 없는 SQL입니다")

    Args:
        sql: 분류할 SQL 문자열.

    Returns:
        (SqlCategory, reason) 튜플.
        category: 4단계 위험도 카테고리.
        reason: 분류 사유 설명 문자열.
    """
    if not sql or not sql.strip():
        return SqlCategory.FORBIDDEN, "SQL이 비어 있습니다."

    cleaned = sql.strip()

    # 주석 탐지 — SQL 인젝션 방지
    if _COMMENT_PATTERN.search(cleaned):
        return SqlCategory.FORBIDDEN, "SQL 주석은 허용되지 않습니다."

    # 다중 문장 차단 — 마지막 세미콜론(트레일링)은 허용
    sql_body = cleaned.rstrip(";").strip()
    if ";" in sql_body:
        return SqlCategory.FORBIDDEN, "다중 SQL 문은 허용되지 않습니다."

    # 1. FORBIDDEN 키워드 체크 (최우선)
    forbidden_match = _FORBIDDEN_KEYWORDS.search(sql_body)
    if forbidden_match:
        keyword = forbidden_match.group(1).upper()
        return SqlCategory.FORBIDDEN, f"차단된 SQL 키워드: {keyword}"

    # 2. NEEDS_APPROVAL 키워드 체크
    approval_match = _NEEDS_APPROVAL_KEYWORDS.search(sql_body)
    if approval_match:
        keyword = approval_match.group(1).upper()
        return SqlCategory.NEEDS_APPROVAL, f"관리자 승인 필요: {keyword} 문"

    # 3. SAFE (SELECT)
    if sql_body.upper().startswith("SELECT"):
        return SqlCategory.SAFE, "안전한 SELECT 쿼리"

    # 4. AUTO_ALLOWED (CREATE, INSERT, UPDATE)
    auto_match = _AUTO_ALLOWED_KEYWORDS.search(sql_body)
    if auto_match:
        keyword = auto_match.group(1).upper()
        return SqlCategory.AUTO_ALLOWED, f"자동 허용: {keyword} 문"

    # 5. 분류 불가 — 안전을 위해 FORBIDDEN
    return SqlCategory.FORBIDDEN, "분류할 수 없는 SQL입니다."
