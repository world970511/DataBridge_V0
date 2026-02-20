"""
질의 라우팅 오케스트레이터 모듈 — 사용자 질의를 의도에 따라 적절한 에이전트로 라우팅.

DataBridge AI 에이전트의 진입점(entry point)입니다. 사용자의 자연어 질의를 분석하여
데이터 조회(SQL), 문서 검색(RAG), 또는 복합(두 에이전트 모두 호출) 의도로 분류한 뒤
해당 에이전트를 호출합니다.

의도 분류는 2단계 하이브리드 방식으로 동작합니다:
1. **규칙 기반 (빠른 경로)**: 키워드 점수 + 카탈로그 테이블명/문서명 매칭.
   점수 차이가 확실하면 LLM 호출 없이 즉시 분류합니다.
2. **LLM 폴백 (느린 경로)**: 규칙 기반으로 판단이 모호하면 Ollama LLM에
   DATA/DOCUMENT/BOTH 분류를 요청합니다.

이 2단계 접근으로 대부분의 질의를 LLM 호출 없이 빠르게 분류하면서,
모호한 경우에만 LLM을 사용하여 비용과 지연을 최소화합니다.

의존 모듈:
    - agent.sql_agent: process() — SQL 에이전트 파이프라인
    - agent.doc_agent: process() — 문서 에이전트 파이프라인
    - agent._llm: generate() — LLM 호출 (의도 분류 폴백용)
    - agent._audit: log_action() — 감사 로그 기록
    - agent.tools.list_tables: get_table_names() — 카탈로그 테이블명 조회
    - agent.tools.list_tables: get_table_tags() — 카탈로그 태그 매핑 조회
    - agent.tools.search_docs: get_document_names() — 카탈로그 문서명 조회

사용 예시:
    from agent.orchestrator import process_query
    result = process_query("sales 테이블에서 총 매출 보여줘")
    print(result["intent"])   # "data"
    print(result["answer"])   # "총 매출은 1,234,000원입니다."
"""

import logging

from agent._llm import generate
from agent._audit import log_action
from agent.tools.list_tables import get_table_names, get_table_tags
from agent.tools.search_docs import get_document_names
from agent.translator import detect_language, translate_if_needed
from agent import sql_agent, doc_agent

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 규칙 기반 의도 분류를 위한 키워드 사전
# --------------------------------------------------------------------------

# 데이터 조회(SQL) 의도를 나타내는 키워드들.
# 점수가 높을수록 해당 의도일 확률이 높습니다.
_DATA_KEYWORDS: dict[str, int] = {
    # SQL 관련 동사/명사
    "조회": 3, "쿼리": 3, "query": 3, "select": 3,'sql':3,'db':3,
    "테이블": 2, "table": 2, "컬럼": 2, "column": 2,
    "데이터": 2, "data": 2, "행": 1, "row": 1,
    # 집계 관련
    "합계": 2, "평균": 2, "최대": 2, "최소": 2, "개수": 2,
    "sum": 2, "avg": 2, "max": 2, "min": 2, "count": 2,
    "총": 2, "전체": 1, "통계": 2,
    # 정렬/필터
    "정렬": 1, "필터": 1, "그룹": 1, "상위": 1, "하위": 1,
    "order": 1, "group": 1, "where": 2, "limit": 1,
    # 데이터 분석
    "매출": 2, "주문": 2, "판매": 2, "재고": 2, "수량": 1,
}

# 문서 검색(RAG) 의도를 나타내는 키워드들.
_DOC_KEYWORDS: dict[str, int] = {
    # 문서 관련
    "문서": 3, "document": 3, "보고서": 3, "report": 3,
    "가이드": 2, "매뉴얼": 2, "manual": 2, "guide": 2,
    # 검색/내용 관련
    "검색": 2, "search": 2, "찾아": 2, "내용": 2,
    "요약": 2, "설명": 2, "분석": 1,
    "어떤": 1, "무엇": 1, "왜": 1, "어떻게": 1,
    # 텍스트 관련
    "텍스트": 2, "본문": 2, "페이지": 2, "챕터": 2,
}

# 목록 조회 의도를 나타내는 키워드들 (파싱 없이 카탈로그만 조회).
_LIST_KEYWORDS: list[str] = [
    "목록", "리스트", "list", "있는 파일", "있는 문서", "있는 테이블",
    "등록된", "어떤 파일", "어떤 문서", "어떤 테이블", "파일들", "문서들",
    "테이블들", "몇 개", "개수", "파일명", "문서명", "테이블명",
]

# 규칙 기반 분류의 확신 임계값.
# |data_score - doc_score| >= _THRESHOLD이면 LLM 폴백 없이 분류합니다.
_SCORE_THRESHOLD = 3

# LLM 의도 분류용 시스템 프롬프트.
_CLASSIFY_SYSTEM_PROMPT = """Classify the intent of the user's question.

Categories:
- DATA: Questions that query/analyze data from database tables (requires SQL)
- DOCUMENT: Questions that search/summarize information from uploaded documents (PDF, DOCX, etc.)
- BOTH: Complex questions that require both data query and document search

You MUST respond with exactly one of DATA, DOCUMENT, or BOTH in uppercase. Do not add any other text.
"""


def process_query(question: str, user_id: str = "system") -> dict:
    """
    사용자 자연어 질의의 의도를 분류하고 적절한 에이전트를 호출하여 응답을 반환.

    처리 흐름:
    1. classify_intent()로 의도를 "data", "document", "composite" 중 하나로 분류
    2. 분류 결과를 audit_log에 기록
    3. 의도에 따라 에이전트 호출:
       - "data"      → sql_agent.process(question, user_id)
       - "document"  → doc_agent.process(question)
       - "composite" → 두 에이전트 모두 호출 후 결과 병합

    Args:
        question: 사용자의 자연어 질의.
                  예: "sales 테이블에서 총 매출 보여줘"
        user_id: 요청한 사용자의 username. 기본값 'system'.
                 SQL 에이전트에서 승인 요청 생성 시 요청자로 기록됩니다.

    Returns:
        에이전트 처리 결과 딕셔너리. 각 에이전트의 결과 형식을 따르되,
        추가로 "intent" 필드가 포함됩니다:
        {
            "intent": str,     — 분류된 의도 ("data", "document", "composite")
            "success": bool,   — 처리 성공 여부
            "answer": str,     — 사용자에게 표시할 응답
            ...                — 에이전트별 추가 필드 (sql, data, sources 등)
        }

        composite(복합) 의도의 경우:
        {
            "intent": "composite",
            "success": bool,
            "answer": str,              — 두 결과를 합친 응답
            "sql_result": dict | None,  — SQL 에이전트 결과
            "doc_result": dict | None,  — 문서 에이전트 결과
        }
    """
    # 사용자 입력 언어 감지 (영어가 아니면 최종 응답 번역)
    query_lang = detect_language(question)

    intent = classify_intent(question)

    log_action(
        action_type="intent_classify",
        query_text=question,
        status="success",
        user_id=user_id,
        metadata={"intent": intent, "query_lang": query_lang},
    )

    logger.info(f"Query intent classified: intent={intent}, lang={query_lang}, question='{question[:50]}...'")

    # 목록 조회 의도 처리 (파싱 없이 카탈로그만 조회)
    if intent.startswith("list"):
        return _process_list_query(intent, question)

    if intent == "data":
        result = sql_agent.process(question, user_id=user_id)
        result["intent"] = "data"
    elif intent == "document":
        result = doc_agent.process(question)
        result["intent"] = "document"
    else:  # composite
        result = _process_composite(question, user_id=user_id)

    # 영어가 아닌 경우 최종 응답을 사용자 언어로 번역
    if query_lang != "en" and result.get("answer"):
        result["answer"] = translate_if_needed(result["answer"], query_lang)

    return result


def classify_intent(question: str) -> str:
    """
    사용자 질의의 의도를 2단계 하이브리드 방식으로 분류.

    0단계 — 목록 조회 (가장 빠른 경로):
        - "목록", "리스트", "등록된" 등 목록 관련 키워드가 포함되면 "list" 반환
        - 문서 내용 파싱 없이 카탈로그만 조회하여 빠르게 응답

    1단계 — 규칙 기반 (빠른 경로):
        - 질의에 포함된 키워드의 점수를 합산 (_DATA_KEYWORDS, _DOC_KEYWORDS)
        - 카탈로그에 등록된 테이블명/문서명이 질의에 포함되어 있으면 가산점 부여 (+5)
        - Rich Catalog 태그가 질의에 포함되어 있으면 추가 가산점 부여 (+3)
        - 두 점수의 차이가 _SCORE_THRESHOLD(기본 3) 이상이면 즉시 분류

    2단계 — LLM 폴백 (느린 경로):
        - 1단계에서 판단이 모호한 경우(점수 차이 < _SCORE_THRESHOLD)
        - Ollama LLM에 DATA/DOCUMENT/BOTH 분류를 요청
        - LLM 응답에서 키워드를 추출하여 분류

    Args:
        question: 사용자의 자연어 질의.

    Returns:
        분류된 의도 문자열: "list", "data", "document", 또는 "composite".
    """
    question_lower = question.lower()

    # 0단계: 목록 조회 키워드 체크 (가장 빠른 경로)
    list_keyword_found = any(kw in question_lower for kw in _LIST_KEYWORDS)
    if list_keyword_found:
        # "pdf 목록", "파일 목록", "문서 목록" 등 → list_doc
        # "테이블 목록", "데이터 목록" 등 → list_data
        if any(kw in question_lower for kw in ["pdf", "파일", "문서", "document", "file"]):
            return "list_doc"
        elif any(kw in question_lower for kw in ["테이블", "table", "데이터", "data"]):
            return "list_data"
        else:
            return "list_all"  # 구분 불명확하면 둘 다 반환

    # 1단계: 키워드 점수 계산
    data_score = sum(
        score for keyword, score in _DATA_KEYWORDS.items()
        if keyword.lower() in question_lower
    )
    doc_score = sum(
        score for keyword, score in _DOC_KEYWORDS.items()
        if keyword.lower() in question_lower
    )

    # 카탈로그 매칭 가산점
    table_names = get_table_names()
    for name in table_names:
        if name.lower() in question_lower:
            data_score += 5  # 테이블명 매칭은 강력한 신호

    # Rich Catalog 태그 매칭 가산점
    table_tags = get_table_tags()
    for _table_name, tags in table_tags.items():
        for tag in tags:
            if tag.lower() in question_lower:
                data_score += 3  # 태그 매칭은 중간 수준의 신호
                break  # 테이블당 최대 1회 가산

    doc_names = get_document_names()
    for name in doc_names:
        # 확장자 제거 후 매칭 (예: "report.pdf" → "report")
        base_name = name.rsplit(".", 1)[0] if "." in name else name
        if base_name.lower() in question_lower or name.lower() in question_lower:
            doc_score += 5  # 문서명 매칭도 강력한 신호

    logger.debug(f"Intent scores: data={data_score}, doc={doc_score}")

    # 점수 차이가 확실하면 즉시 분류
    score_diff = abs(data_score - doc_score)
    if score_diff >= _SCORE_THRESHOLD:
        if data_score > doc_score:
            return "data"
        else:
            return "document"

    # 한쪽만 점수가 있는 경우
    if data_score > 0 and doc_score == 0:
        return "data"
    if doc_score > 0 and data_score == 0:
        return "document"

    # 2단계: LLM 폴백
    return _classify_with_llm(question)


def _classify_with_llm(question: str) -> str:
    """
    LLM을 사용하여 질의 의도를 DATA/DOCUMENT/BOTH로 분류.

    오케스트레이터용 모델(purpose="orchestrator")을 사용하여 분류합니다.
    상용 모델이 설정된 경우 더 빠르고 정확한 분류가 가능합니다.

    Args:
        question: 사용자의 자연어 질의.

    Returns:
        분류된 의도: "data", "document", 또는 "composite".
    """
    response = generate(
        prompt=f"User question: {question}",
        system=_CLASSIFY_SYSTEM_PROMPT,
        purpose="orchestrator",  # 오케스트레이터용 모델 사용
        temperature=0.0,
        timeout=90,  # 모델 로딩 시간 고려 (CPU 환경에서 첫 응답까지 30초+ 소요 가능)
    )

    if not response:
        logger.warning("LLM intent classification failed, defaulting to 'data'")
        return "data"

    response_upper = response.strip().upper()

    if "BOTH" in response_upper:
        return "composite"
    elif "DOCUMENT" in response_upper:
        return "document"
    elif "DATA" in response_upper:
        return "data"
    else:
        logger.warning(
            f"LLM intent classification unclear: '{response}', defaulting to 'data'"
        )
        return "data"


def _process_composite(question: str, user_id: str = "system") -> dict:
    """
    복합(composite) 의도 질의를 처리하여 SQL 에이전트와 문서 에이전트 결과를 병합.

    두 에이전트를 순차적으로 호출하고, 각각의 결과를 합쳐 하나의 응답으로 구성합니다.
    한쪽 에이전트가 실패해도 다른 쪽의 결과는 정상적으로 포함됩니다.

    Args:
        question: 사용자의 자연어 질의.
        user_id: 요청한 사용자의 username.

    Returns:
        복합 결과 딕셔너리:
        {
            "intent": "composite",
            "success": bool,           — 하나 이상의 에이전트가 성공했는지
            "answer": str,             — 두 결과를 합친 응답
            "sql_result": dict | None, — SQL 에이전트 결과
            "doc_result": dict | None, — 문서 에이전트 결과
        }
    """
    sql_result = sql_agent.process(question, user_id=user_id)
    doc_result = doc_agent.process(question)

    # 두 결과를 합쳐서 응답 구성
    answer_parts = []

    if sql_result["success"] and sql_result.get("answer"):
        answer_parts.append(f"📊 데이터 조회 결과:\n{sql_result['answer']}")

    if doc_result["success"] and doc_result.get("answer"):
        answer_parts.append(f"📄 문서 검색 결과:\n{doc_result['answer']}")

    if not answer_parts:
        combined_answer = "데이터 조회와 문서 검색 모두 결과를 얻지 못했습니다."
    else:
        combined_answer = "\n\n---\n\n".join(answer_parts)

    overall_success = sql_result["success"] or doc_result["success"]

    log_action(
        action_type="composite_result",
        query_text=question,
        result_summary=combined_answer[:500],
        status="success" if overall_success else "failed",
        metadata={
            "sql_success": sql_result["success"],
            "doc_success": doc_result["success"],
        },
    )

    return {
        "intent": "composite",
        "success": overall_success,
        "answer": combined_answer,
        "sql_result": sql_result,
        "doc_result": doc_result,
    }


def _process_list_query(intent: str, question: str) -> dict:
    """
    목록 조회 의도를 처리하여 카탈로그 정보를 반환 (문서 파싱 없음).

    파일/문서 목록을 요청하면 카탈로그 DB에서 메타데이터만 조회하여 반환합니다.
    실제 파일 내용을 파싱하지 않으므로 대용량 PDF도 즉시 응답합니다.

    Args:
        intent: "list_doc", "list_data", 또는 "list_all".
        question: 사용자의 원본 질의 (로깅용).

    Returns:
        목록 조회 결과 딕셔너리:
        {
            "intent": str,
            "success": bool,
            "answer": str,    — 포맷팅된 목록 텍스트
            "tables": list,   — 테이블 정보 (list_data, list_all)
            "documents": list — 문서 정보 (list_doc, list_all)
        }
    """
    from catalog.catalog import list_tables, list_documents

    tables = []
    documents = []
    answer_parts = []

    # 테이블 목록 조회 (Rich Catalog 정보 포함)
    if intent in ("list_data", "list_all"):
        try:
            tables = list_tables()
            if tables:
                table_lines = []
                for t in tables:
                    line = f"  - {t['table_name']} ({t['row_count']} rows, {t['column_count']} columns)"
                    desc = t.get("description")
                    tags = t.get("tags")
                    if desc:
                        line += f"\n    Description: {desc}"
                    if tags:
                        line += f"\n    Tags: {', '.join(tags)}"
                    table_lines.append(line)
                answer_parts.append(f"📊 Registered Tables ({len(tables)}):\n" + "\n".join(table_lines))
            else:
                answer_parts.append("📊 No registered tables.")
        except Exception as e:
            logger.error(f"Failed to list tables: {e}")
            answer_parts.append("📊 Error occurred while listing tables.")

    # 문서 목록 조회
    if intent in ("list_doc", "list_all"):
        try:
            documents = list_documents()
            if documents:
                doc_lines = [f"  - {d['doc_name']} ({d['file_type']}, {d['chunk_count']} chunks)" for d in documents]
                answer_parts.append(f"📄 Registered Documents ({len(documents)}):\n" + "\n".join(doc_lines))
            else:
                answer_parts.append("📄 No registered documents.")
        except Exception as e:
            logger.error(f"Failed to list documents: {e}")
            answer_parts.append("📄 Error occurred while listing documents.")

    answer = "\n\n".join(answer_parts) if answer_parts else "목록 정보를 가져올 수 없습니다."

    log_action(
        action_type="list_query",
        query_text=question,
        result_summary=answer[:500],
        status="success",
        metadata={"intent": intent, "table_count": len(tables), "doc_count": len(documents)},
    )

    return {
        "intent": intent,
        "success": True,
        "answer": answer,
        "tables": tables,
        "documents": documents,
        "agent": "orchestrator",
    }
