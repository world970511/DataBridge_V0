"""
DataBridge AI 에이전트 패키지.

orchestrator.process_query()를 통해 사용자의 자연어 질의를 분석하고,
질의 의도(데이터 조회 / 문서 검색 / 복합)에 따라 적절한 에이전트
(SQL 에이전트 또는 문서 에이전트)로 라우팅하여 응답을 생성합니다.

사용 예시:
    from agent import process_query
    result = process_query("sales 테이블에서 총 매출 보여줘")
    print(result["answer"])
"""

from agent.orchestrator import process_query

__all__ = ["process_query"]
