"""
DataBridge 채팅 페이지.

자연어 질의를 입력받아 AI 에이전트(SQL/문서)를 호출하고,
결과를 채팅 형식으로 표시합니다. SQL 실행 결과는 DataFrame 테이블로 표시됩니다.

Streamlit session_state 키:
    - "chat_history": list[dict] — 채팅 이력 [{role, content, data?, sql?, ...}]

의존 모듈:
    - agent.orchestrator: process_query() — 질의 라우팅 + 에이전트 호출
    - auth.session: get_current_user() — 현재 로그인 사용자 조회
"""

import logging

import streamlit as st
import pandas as pd

from agent.orchestrator import process_query
from auth.session import get_current_user

logger = logging.getLogger(__name__)


def _init_chat_state():
    """채팅 세션 상태 초기화."""
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []


def render_chat_page():
    """
    채팅 페이지를 렌더링.

    1. 채팅 이력을 st.chat_message로 순차 표시
    2. st.chat_input으로 사용자 입력 대기
    3. 입력 시 process_query() 호출 → 결과를 이력에 추가
    4. SQL 결과가 있으면 pandas DataFrame으로 테이블 표시
    """
    _init_chat_state()

    st.title("💬 Chat")
    st.caption("자연어로 데이터를 조회하거나 문서를 검색하세요.")

    # 기존 채팅 이력 표시
    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

            # SQL 결과 테이블 표시
            if msg.get("data") and len(msg["data"]) > 0:
                df = pd.DataFrame(msg["data"])
                st.dataframe(df, use_container_width=True)

            # SQL 코드 표시
            if msg.get("sql"):
                with st.expander("🔍 실행된 SQL"):
                    st.code(msg["sql"], language="sql")

    # 사용자 입력
    user_input = st.chat_input("질문을 입력하세요...")

    if user_input:
        # 사용자 메시지 추가
        st.session_state["chat_history"].append({
            "role": "user",
            "content": user_input,
        })

        with st.chat_message("user"):
            st.markdown(user_input)

        # AI 응답 생성
        with st.chat_message("assistant"):
            with st.spinner("🤔 생각 중..."):
                user = get_current_user()
                user_id = user["username"] if user else "system"

                try:
                    result = process_query(user_input, user_id=user_id)
                except Exception as e:
                    logger.error(f"process_query failed: {e}")
                    result = {
                        "success": False,
                        "answer": f"시스템 오류가 발생했습니다: {str(e)}",
                        "data": [],
                        "sql": None,
                    }

            # 응답 표시
            st.markdown(result.get("answer", "응답을 생성하지 못했습니다."))

            # SQL 결과 테이블
            data = result.get("data", [])
            if data and len(data) > 0:
                df = pd.DataFrame(data)
                st.dataframe(df, use_container_width=True)

            # SQL 코드
            sql = result.get("sql")
            if sql:
                with st.expander("🔍 실행된 SQL"):
                    st.code(sql, language="sql")

        # 어시스턴트 메시지 이력에 추가
        st.session_state["chat_history"].append({
            "role": "assistant",
            "content": result.get("answer", ""),
            "data": data,
            "sql": sql,
            "success": result.get("success", False),
            "intent": result.get("intent"),
            "sql_category": result.get("sql_category"),
            "approval_id": result.get("approval_id"),
        })
