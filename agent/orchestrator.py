"""
질의 라우팅 오케스트레이터 모듈 — 사용자 질의를 의도에 따라 적절한 에이전트로 라우팅.

DataBridge AI 에이전트의 진입점(entry point)입니다. 사용자의 자연어 질의를 분석하여
데이터 조회(SQL), 문서 검색(RAG), 이미지 검색/분석, 또는 복합(다중 에이전트 + LLM 종합 추론)
의도로 분류한 뒤 해당 에이전트를 호출합니다.

의도 분류는 2단계 하이브리드 방식으로 동작합니다:
1. **규칙 기반 (빠른 경로)**: 키워드 점수 + 카탈로그 테이블명/문서명/이미지명 매칭.
   점수 차이가 확실하면 LLM 호출 없이 즉시 분류합니다.
2. **LLM 폴백 (느린 경로)**: 규칙 기반으로 판단이 모호하면 LLM에
   DATA/DOCUMENT/IMAGE/BOTH 분류를 요청합니다.

이 2단계 접근으로 대부분의 질의를 LLM 호출 없이 빠르게 분류하면서,
모호한 경우에만 LLM을 사용하여 비용과 지연을 최소화합니다.

의존 모듈:
    - agent.sql_agent: process() — SQL 에이전트 파이프라인
    - agent.doc_agent: process() — 문서 에이전트 파이프라인
    - agent.image_agent: process() — 이미지 에이전트 파이프라인
    - agent._llm: generate() — LLM 호출 (의도 분류 폴백용)
    - agent._audit: log_action() — 감사 로그 기록
    - agent.tools.list_tables: get_table_names() — 카탈로그 테이블명 조회
    - agent.tools.list_tables: get_table_tags() — 카탈로그 태그 매핑 조회
    - agent.tools.search_docs: get_document_names() — 카탈로그 문서명 조회
    - agent.tools.search_images: get_image_names() — 카탈로그 이미지명 조회

사용 예시:
    from agent.orchestrator import process_query
    result = process_query("sales 테이블에서 총 매출 보여줘")
    print(result["intent"])   # "data"
    print(result["answer"])   # "총 매출은 1,234,000원입니다."
"""

import logging

from agent._llm import generate
from agent._audit import log_action
from agent.tools.list_tables import get_table_names, get_table_tags, get_table_column_info
from agent.tools.search_docs import get_document_names
from agent.tools.search_images import get_image_names
from agent.translator import detect_language, translate_if_needed
from agent import sql_agent, doc_agent, image_agent

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
    # 마트/생성 관련
    "마트": 3, "mart": 3, "구축": 2, "생성": 2, "만들어": 1,
    "추출": 2, "가공": 2, "변환": 2, "집계표": 2,
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
    # 문서 관련 동사
    "참고": 2, "참조": 2, "근거": 2, "기반": 1, "바탕": 1,
}

# 이미지 검색/분석 의도를 나타내는 키워드들.
_IMAGE_KEYWORDS: dict[str, int] = {
    # 이미지 관련 명사
    "이미지": 3, "image": 3, "사진": 3, "photo": 3, "picture": 3,
    "그림": 2, "스크린샷": 2, "screenshot": 2,
    # 이미지 분석 관련
    "유사": 2, "similar": 2, "비슷한": 2, "닮은": 2,
    "중복": 3, "duplicate": 3, "중복된": 3,
    "그룹": 2, "클러스터": 3, "cluster": 3, "분류": 2, "묶어": 2,
    # EXIF/메타데이터
    "exif": 3, "카메라": 2, "렌즈": 2, "촬영": 2, "gps": 2,
    "해상도": 2, "resolution": 2,
    # 이미지 파일 확장자 (질의에 포함될 수 있음)
    ".jpg": 2, ".jpeg": 2, ".png": 2, ".tiff": 2, ".webp": 2,
    # 시각적 검색
    "썸네일": 2, "thumbnail": 2, "미리보기": 1,
}

# 목록 조회 의도를 나타내는 키워드들 (파싱 없이 카탈로그만 조회).
_LIST_KEYWORDS: list[str] = [
    "목록", "리스트", "list", "있는 파일", "있는 문서", "있는 테이블",
    "등록된", "어떤 파일", "어떤 문서", "어떤 테이블", "파일들", "문서들",
    "테이블들", "몇 개", "개수", "파일명", "문서명", "테이블명",
    "있는 이미지", "어떤 이미지", "이미지들", "이미지명", "사진들",
]

# 메타데이터 조회 의도를 나타내는 키워드들 (SQL 없이 카탈로그 직접 응답).
# "컬럼 뭐야", "스키마 알려줘", "어떤 필드" 등 테이블 구조를 묻는 질의.
_META_KEYWORDS: list[str] = [
    "컬럼", "column", "columns", "필드", "field", "fields",
    "스키마", "schema", "구조", "structure",
    "어떤 컬럼", "무슨 컬럼", "컬럼 목록", "컬럼이 뭐",
    "어떤 필드", "무슨 필드",
]

# 마트 생성 의도를 나타내는 키워드들.
# "마트 만들어줘", "집계표 구축해줘" 등 마트 빌더를 호출해야 하는 질의.
_MART_KEYWORDS: list[str] = [
    "마트 만들", "마트 생성", "마트 구축", "마트를 만들", "마트를 생성", "마트를 구축",
    "mart 만들", "mart 생성", "mart 구축",
    "집계표 만들", "집계표 생성", "집계표 구축",
    "create mart", "build mart",
]

# 배치 작업 의도를 나타내는 키워드들.
# "배치 작업 등록", "스케줄 설정" 등 배치 관리를 호출해야 하는 질의.
_JOB_KEYWORDS: list[str] = [
    "배치", "batch", "스케줄", "schedule", "cron",
    "주기적", "정기적", "자동 실행", "자동실행",
    "작업 등록", "작업 생성", "작업 조회", "작업 목록",
    "작업 삭제", "작업 중지", "작업 실행",
]

# 규칙 기반 분류의 확신 임계값.
# |data_score - doc_score| >= _THRESHOLD이면 LLM 폴백 없이 분류합니다.
_SCORE_THRESHOLD = 3

# LLM 의도 분류용 시스템 프롬프트.
_CLASSIFY_SYSTEM_PROMPT = """Classify the intent of the user's question.

Categories:
- DATA: Questions that query/analyze data from database tables (requires SQL)
- DOCUMENT: Questions that search/summarize information from uploaded documents (PDF, DOCX, etc.)
- IMAGE: Questions about images — finding similar images, grouping/clustering, duplicate detection, EXIF metadata
- BOTH: Complex questions that require both data query and document search

You MUST respond with exactly one of DATA, DOCUMENT, IMAGE, or BOTH in uppercase. Do not add any other text.
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

    # 메타데이터 조회 의도 처리 (컬럼 정보 등 → 카탈로그 직접 응답)
    if intent.startswith("meta_columns:"):
        table_name = intent.split(":", 1)[1]
        return _process_meta_query(table_name, question)

    # 목록 조회 의도 처리 (파싱 없이 카탈로그만 조회)
    if intent.startswith("list"):
        return _process_list_query(intent, question)

    # 마트 생성 의도 처리
    if intent == "create_mart":
        return _process_mart_query(question, user_id)

    # 배치 작업 의도 처리
    if intent == "manage_job":
        return _process_job_query(question, user_id)

    if intent == "data":
        result = sql_agent.process(question, user_id=user_id)
        result["intent"] = "data"
    elif intent == "document":
        result = doc_agent.process(question)
        result["intent"] = "document"
    elif intent == "image":
        sub_intent = _classify_image_sub_intent(question)
        result = image_agent.process(question, sub_intent=sub_intent)
        result["intent"] = "image"
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
    # 단, 특정 문서명/테이블명이 포함된 경우는 단순 목록이 아니라
    # 시맨틱 검색 의도일 수 있으므로, 카탈로그 매칭과 함께 판단합니다.
    list_keyword_found = any(kw in question_lower for kw in _LIST_KEYWORDS)
    if list_keyword_found:
        # 특정 문서명이 질의에 포함되어 있으면 → 단순 목록이 아닌 문서 검색 의도
        doc_names = get_document_names()
        has_specific_doc = any(
            name.lower() in question_lower or
            (name.rsplit(".", 1)[0].lower() if "." in name else name.lower()) in question_lower
            for name in doc_names
        )
        if has_specific_doc:
            # "조달청 서면업무보고.pdf와 비슷한 문서 목록" → document 의도
            return "document"

        # 특정 테이블명이 질의에 포함되어 있으면 → data 의도
        table_names = get_table_names()
        has_specific_table = any(name.lower() in question_lower for name in table_names)
        if has_specific_table:
            return "data"

        # 특정 이미지명이 질의에 포함되어 있으면 → image 의도
        image_names = get_image_names()
        has_specific_image = any(
            name.lower() in question_lower or
            (name.rsplit(".", 1)[0].lower() if "." in name else name.lower()) in question_lower
            for name in image_names
        )
        if has_specific_image:
            return "image"

        # 특정 항목 없이 일반 목록 요청
        # "pdf 목록", "파일 목록", "문서 목록" 등 → list_doc
        # "테이블 목록", "데이터 목록" 등 → list_data
        # "이미지 목록", "사진 목록" 등 → list_image
        if any(kw in question_lower for kw in ["이미지", "image", "사진", "photo", "picture"]):
            return "list_image"
        elif any(kw in question_lower for kw in ["pdf", "파일", "문서", "document", "file"]):
            return "list_doc"
        elif any(kw in question_lower for kw in ["테이블", "table", "데이터", "data"]):
            return "list_data"
        else:
            return "list_all"  # 구분 불명확하면 전부 반환

    # 0.5단계: 메타데이터 질의 체크 (컬럼 조회 등 → 카탈로그 직접 응답)
    # "t_xxx 테이블의 컬럼이 뭐야?" 같은 질의는 SQL/LLM 없이 카탈로그에서 바로 응답
    meta_keyword_found = any(kw in question_lower for kw in _META_KEYWORDS)
    if meta_keyword_found:
        table_names = get_table_names()
        matched_table = None
        for name in table_names:
            if name.lower() in question_lower:
                matched_table = name
                break
        if matched_table:
            return f"meta_columns:{matched_table}"

    # 0.7단계: 마트 생성 / 배치 작업 의도 체크
    # "마트 만들어줘", "배치 작업 등록" 같은 명시적 요청은 전용 도구로 라우팅
    mart_keyword_found = any(kw in question_lower for kw in _MART_KEYWORDS)
    if mart_keyword_found:
        return "create_mart"

    job_keyword_found = any(kw in question_lower for kw in _JOB_KEYWORDS)
    if job_keyword_found:
        return "manage_job"

    # 1단계: 키워드 점수 계산 (3-way: data vs doc vs image)
    data_score = sum(
        score for keyword, score in _DATA_KEYWORDS.items()
        if keyword.lower() in question_lower
    )
    doc_score = sum(
        score for keyword, score in _DOC_KEYWORDS.items()
        if keyword.lower() in question_lower
    )
    image_score = sum(
        score for keyword, score in _IMAGE_KEYWORDS.items()
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

    # 이미지 카탈로그 매칭 가산점
    image_names = get_image_names()
    for name in image_names:
        base_name = name.rsplit(".", 1)[0] if "." in name else name
        if base_name.lower() in question_lower or name.lower() in question_lower:
            image_score += 5  # 이미지명 매칭도 강력한 신호

    logger.debug(f"Intent scores: data={data_score}, doc={doc_score}, image={image_score}")

    # 복합(composite) 의도 감지: 문서+데이터 양쪽 모두 의미 있는 점수일 때
    # "문서 내용을 바탕으로 통계에서 필요한 부분만 추출해줘" 같은 교차 질의
    _COMPOSITE_MIN_SCORE = 2  # 양쪽 모두 이 점수 이상이어야 composite 후보
    if data_score >= _COMPOSITE_MIN_SCORE and doc_score >= _COMPOSITE_MIN_SCORE:
        # 이미지 점수가 data/doc 양쪽보다 높으면 composite 대신 image 우선
        if image_score > data_score and image_score > doc_score:
            logger.info(
                f"Image intent overrides composite: image={image_score} > "
                f"data={data_score}, doc={doc_score}"
            )
            return "image"
        # 양쪽 모두 충분한 점수 → composite로 분류
        # (이미지는 composite 대상에서 제외: 데이터+문서 교차만 지원)
        logger.info(
            f"Composite intent detected: data={data_score}, doc={doc_score} "
            f"(both >= {_COMPOSITE_MIN_SCORE})"
        )
        return "composite"

    # 3-way 점수 비교: 최고 점수가 나머지보다 _SCORE_THRESHOLD 이상 높으면 즉시 분류
    scores = {"data": data_score, "document": doc_score, "image": image_score}
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_intent, top_score = sorted_scores[0]
    second_score = sorted_scores[1][1]

    if top_score > 0 and (top_score - second_score) >= _SCORE_THRESHOLD:
        return top_intent

    # 한쪽만 점수가 있는 경우
    nonzero = [(intent, s) for intent, s in scores.items() if s > 0]
    if len(nonzero) == 1:
        return nonzero[0][0]

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
    )

    if not response:
        logger.warning("LLM intent classification failed, defaulting to 'data'")
        return "data"

    response_upper = response.strip().upper()

    if "BOTH" in response_upper:
        return "composite"
    elif "IMAGE" in response_upper:
        return "image"
    elif "DOCUMENT" in response_upper:
        return "document"
    elif "DATA" in response_upper:
        return "data"
    else:
        logger.warning(
            f"LLM intent classification unclear: '{response}', defaulting to 'data'"
        )
        return "data"


def _classify_image_sub_intent(question: str) -> str:
    """
    이미지 질의의 세부 의도를 키워드 기반으로 분류.

    Args:
        question: 사용자의 자연어 질의.

    Returns:
        세부 의도: "search", "group", "duplicates", "list", 또는 "info".
    """
    q = question.lower()

    # 중복 탐지 (가장 구체적인 키워드 먼저)
    if any(kw in q for kw in ["중복", "duplicate", "중복된", "같은 이미지", "동일"]):
        return "duplicates"

    # 그룹핑/클러스터링
    if any(kw in q for kw in ["그룹", "묶어", "분류", "클러스터", "cluster", "정리"]):
        return "group"

    # 목록 조회
    if any(kw in q for kw in ["목록", "리스트", "list", "전체", "모두", "몇 개"]):
        return "list"

    # 상세 정보 (EXIF 등)
    if any(kw in q for kw in [
        "정보", "info", "exif", "메타", "카메라", "렌즈",
        "촬영", "해상도", "크기", "상세",
    ]):
        return "info"

    # 기본: 유사 이미지 검색
    return "search"


def _process_composite(question: str, user_id: str = "system") -> dict:
    """
    복합(composite) 의도 질의를 '수집 + LLM 종합 추론' 파이프라인으로 처리.

    처리 흐름:
    1. 수집 단계: SQL 에이전트와 문서 에이전트를 각각 호출하여 개별 결과를 수집.
    2. 종합 추론 단계: 수집된 결과를 하나의 컨텍스트로 합쳐 LLM에 전달하여
       교차 참조 기반의 통합 답변을 생성.

    LLM이 두 소스의 정보를 교차 분석하여
    "문서 내용을 바탕으로 통계에서 필요한 부분만 추출" 같은 복합 질의에 대해
    의미 있는 종합 답변을 생성합니다.

    한쪽 에이전트만 성공한 경우 LLM 종합 없이 해당 결과를 직접 반환합니다.

    Args:
        question: 사용자의 자연어 질의.
        user_id: 요청한 사용자의 username.

    Returns:
        복합 결과 딕셔너리:
        {
            "intent": "composite",
            "success": bool,           — 하나 이상의 에이전트가 성공했는지
            "answer": str,             — LLM 종합 추론 응답 (또는 단일 결과)
            "sql_result": dict | None, — SQL 에이전트 원본 결과
            "doc_result": dict | None, — 문서 에이전트 원본 결과
        }
    """
    # ── 1단계: 수집 ──
    logger.info("Composite pipeline: collecting results from agents...")

    sql_result = sql_agent.process(question, user_id=user_id)
    doc_result = doc_agent.process(question)

    sql_ok = sql_result.get("success", False) and sql_result.get("answer")
    doc_ok = doc_result.get("success", False) and doc_result.get("answer")

    overall_success = sql_ok or doc_ok

    # ── 2단계: 종합 추론 ──
    if sql_ok and doc_ok:
        # 양쪽 모두 결과가 있으면 LLM 종합 추론 수행
        logger.info("Composite pipeline: both agents returned results, synthesizing...")
        combined_answer = _synthesize_composite(
            question, sql_result["answer"], doc_result["answer"]
        )
    elif sql_ok:
        combined_answer = f"📊 데이터 조회 결과:\n{sql_result['answer']}\n\n(관련 문서를 찾지 못했습니다.)"
    elif doc_ok:
        combined_answer = f"📄 문서 검색 결과:\n{doc_result['answer']}\n\n(관련 데이터를 조회하지 못했습니다.)"
    else:
        combined_answer = "데이터 조회와 문서 검색 모두 결과를 얻지 못했습니다."

    log_action(
        action_type="composite_result",
        query_text=question,
        result_summary=combined_answer[:500],
        status="success" if overall_success else "failed",
        metadata={
            "sql_success": bool(sql_ok),
            "doc_success": bool(doc_ok),
            "synthesis_used": bool(sql_ok and doc_ok),
        },
    )

    return {
        "intent": "composite",
        "success": overall_success,
        "answer": combined_answer,
        "sql_result": sql_result,
        "doc_result": doc_result,
    }


# LLM 종합 추론용 시스템 프롬프트.
_COMPOSITE_SYNTHESIS_SYSTEM = """You are a data analysis assistant that synthesizes information from multiple sources.
You will be given:
1. A user question
2. Database query results (structured data from SQL)
3. Document search results (text from PDFs/documents)

Your task:
- Cross-reference the data and document information to answer the user's question comprehensively.
- Highlight connections, patterns, or insights that emerge from combining both sources.
- If the user asks for extraction, filtering, or report preparation, focus on the relevant intersection of both sources.
- Structure your answer clearly with sections if needed.
- Answer in the same language as the user's question.
- Be concise but thorough — do not simply repeat the raw results.
"""


def _synthesize_composite(question: str, data_answer: str, doc_answer: str) -> str:
    """
    SQL 에이전트와 문서 에이전트의 결과를 LLM에 전달하여 교차 참조 기반 종합 답변을 생성.

    두 소스의 정보를 하나의 컨텍스트로 합쳐 LLM이 교차 분석하도록 합니다.
    LLM 호출 실패 시 기존 방식(단순 병합)으로 폴백합니다.

    Args:
        question: 사용자의 원본 질의.
        data_answer: SQL 에이전트의 응답 텍스트.
        doc_answer: 문서 에이전트의 응답 텍스트.

    Returns:
        LLM이 생성한 종합 답변 문자열. 실패 시 단순 병합 결과.
    """
    # 컨텍스트 크기 제한 (너무 길면 LLM 성능 저하)
    max_source_len = 3000
    data_text = data_answer[:max_source_len] if len(data_answer) > max_source_len else data_answer
    doc_text = doc_answer[:max_source_len] if len(doc_answer) > max_source_len else doc_answer

    synthesis_prompt = f"""사용자 질문: {question}

--- 📊 데이터 조회 결과 ---
{data_text}

--- 📄 문서 검색 결과 ---
{doc_text}

위 두 소스의 정보를 종합하여 사용자의 질문에 답변해 주세요.
두 소스 간의 연관성이나 교차 분석이 가능한 부분을 중점적으로 다루어 주세요."""

    try:
        synthesized = generate(
            prompt=synthesis_prompt,
            system=_COMPOSITE_SYNTHESIS_SYSTEM,
            purpose="agent",  # 데이터 처리 작업이므로 agent 모델 사용
            temperature=0.2,
        )

        if synthesized and len(synthesized.strip()) > 20:
            logger.info(f"Composite synthesis completed: {len(synthesized)} chars")
            return f"🔗 종합 분석 결과:\n\n{synthesized}"
        else:
            logger.warning("Composite synthesis returned empty/short result, falling back")

    except Exception as e:
        logger.error(f"Composite synthesis failed: {e}")

    # 폴백: 기존 단순 병합 방식
    return (
        f"📊 데이터 조회 결과:\n{data_answer}\n\n"
        f"---\n\n"
        f"📄 문서 검색 결과:\n{doc_answer}"
    )


def _process_meta_query(table_name: str, question: str) -> dict:
    """
    메타데이터 조회 의도를 처리하여 카탈로그에서 컬럼 정보를 직접 반환 (LLM 불필요).

    "컬럼이 뭐야", "스키마 알려줘" 같은 테이블 구조 질의는
    SQL 생성이나 LLM 호출 없이 카탈로그에서 즉시 응답합니다.

    Args:
        table_name: 조회할 테이블명.
        question: 사용자의 원본 질의 (로깅용).

    Returns:
        카탈로그 기반 컬럼 정보 응답 딕셔너리.
    """
    column_info = get_table_column_info(table_name)

    if column_info:
        answer = column_info
    else:
        answer = f"'{table_name}' 테이블의 컬럼 정보를 찾을 수 없습니다."

    log_action(
        action_type="meta_query",
        query_text=question,
        result_summary=answer[:500],
        status="success",
        metadata={"table_name": table_name, "intent": "meta_columns"},
    )

    return {
        "intent": "meta_columns",
        "success": True,
        "answer": answer,
        "tables": [],
        "documents": [],
        "agent": "orchestrator",
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
    from catalog.catalog import list_tables, list_documents, list_images

    tables = []
    documents = []
    images = []
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

    # 이미지 목록 조회
    if intent in ("list_image", "list_all"):
        try:
            images = list_images()
            if images:
                img_lines = []
                for img in images:
                    name = img.get("image_name", "unknown")
                    ftype = img.get("file_type", "")
                    camera = img.get("camera_model", "")
                    line = f"  - {name}"
                    details = []
                    if ftype:
                        details.append(ftype.upper())
                    if camera:
                        details.append(f"카메라: {camera}")
                    if details:
                        line += f" ({', '.join(details)})"
                    img_lines.append(line)
                answer_parts.append(f"🖼️ Registered Images ({len(images)}):\n" + "\n".join(img_lines))
            else:
                answer_parts.append("🖼️ No registered images.")
        except Exception as e:
            logger.error(f"Failed to list images: {e}")
            answer_parts.append("🖼️ Error occurred while listing images.")

    answer = "\n\n".join(answer_parts) if answer_parts else "목록 정보를 가져올 수 없습니다."

    log_action(
        action_type="list_query",
        query_text=question,
        result_summary=answer[:500],
        status="success",
        metadata={
            "intent": intent,
            "table_count": len(tables),
            "doc_count": len(documents),
            "image_count": len(images),
        },
    )

    return {
        "intent": intent,
        "success": True,
        "answer": answer,
        "tables": tables,
        "documents": documents,
        "images": images,
        "agent": "orchestrator",
    }


def _process_mart_query(question: str, user_id: str) -> dict:
    """
    마트 생성 의도를 처리하여 데이터 마트를 생성.

    create_mart 도구를 호출하여 LLM 기반 CREATE TABLE AS SELECT를 생성·실행합니다.

    Args:
        question: 사용자의 마트 생성 요청.
        user_id: 요청자 username.

    Returns:
        마트 생성 결과 딕셔너리.
    """
    from agent.tools.create_mart import create_mart

    result = create_mart(question=question, user_id=user_id)
    result["intent"] = "create_mart"
    return result


def _process_job_query(question: str, user_id: str) -> dict:
    """
    배치 작업 관리 의도를 처리.

    질의 내용에 따라 배치 작업 목록 조회, 상태 확인, 수동 실행 등을 수행합니다.
    현재는 목록 조회와 상태 확인을 지원하며,
    작업 생성/수정/삭제는 관리자 UI를 통해 처리하도록 안내합니다.

    Args:
        question: 사용자의 배치 작업 관련 질의.
        user_id: 요청자 username.

    Returns:
        배치 작업 관리 결과 딕셔너리.
    """
    from agent.tools.manage_jobs import list_jobs, get_recent_history

    question_lower = question.lower()

    # 실행 이력 조회
    if any(kw in question_lower for kw in ["이력", "히스토리", "history", "로그", "log", "결과"]):
        history = get_recent_history(limit=20)
        if history:
            lines = []
            for h in history:
                status_icon = "✅" if h["status"] == "success" else "❌"
                job_name = h.get("job_name", f"job#{h['job_id']}")
                started = h["started_at"].strftime("%Y-%m-%d %H:%M") if h.get("started_at") else "-"
                elapsed = f"{h['execution_time']:.1f}s" if h.get("execution_time") else "-"
                lines.append(f"  {status_icon} {job_name} | {started} | {elapsed} | {h.get('rows_affected', 0)}행")
            answer = f"📋 최근 배치 작업 실행 이력 ({len(history)}건):\n" + "\n".join(lines)
        else:
            answer = "📋 배치 작업 실행 이력이 없습니다."

        return {
            "intent": "manage_job",
            "success": True,
            "answer": answer,
            "agent": "orchestrator",
        }

    # 기본: 작업 목록 조회
    jobs = list_jobs()
    if jobs:
        lines = []
        for j in jobs:
            active_icon = "🟢" if j["is_active"] else "🔴"
            last_status = j.get("last_status") or "미실행"
            last_run = j["last_run_at"].strftime("%Y-%m-%d %H:%M") if j.get("last_run_at") else "미실행"
            lines.append(
                f"  {active_icon} [{j['id']}] {j['job_name']}\n"
                f"      cron: {j['cron_expr']} | 마지막: {last_run} ({last_status})\n"
                f"      {j.get('description', '') or ''}"
            )
        answer = f"📋 등록된 배치 작업 ({len(jobs)}건):\n" + "\n".join(lines)
    else:
        answer = "📋 등록된 배치 작업이 없습니다."

    answer += "\n\n💡 배치 작업 생성/수정/삭제는 관리자 페이지에서 가능합니다."

    log_action(
        action_type="job_query",
        query_text=question,
        result_summary=answer[:500],
        status="success",
        user_id=user_id,
        metadata={"job_count": len(jobs)},
    )

    return {
        "intent": "manage_job",
        "success": True,
        "answer": answer,
        "agent": "orchestrator",
    }
