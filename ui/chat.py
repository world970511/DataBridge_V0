"""
DataBridge 채팅 페이지.

자연어 질의를 입력받아 AI 에이전트(SQL/문서/이미지)를 호출하고,
결과를 채팅 형식으로 표시합니다. SQL 실행 결과는 DataFrame 테이블로,
이미지 검색 결과는 썸네일 그리드로 표시됩니다.

Streamlit session_state 키:
    - "chat_history": list[dict] — 채팅 이력 [{role, content, data?, sql?, images?, ...}]

의존 모듈:
    - agent.orchestrator: process_query() — 질의 라우팅 + 에이전트 호출
    - auth.session: get_current_user() — 현재 로그인 사용자 조회
"""

import logging
from pathlib import Path

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
    5. 이미지 결과가 있으면 썸네일 그리드로 표시
    """
    _init_chat_state()

    st.title("💬 Chat")
    st.caption("자연어로 데이터를 조회하거나 문서/이미지를 검색하세요.")

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

            # 이미지 결과 표시
            if msg.get("images"):
                _render_image_results(msg["images"])

            # 이미지 그룹 표시
            if msg.get("groups"):
                _render_image_groups(msg["groups"])

            # 중복 이미지 표시
            if msg.get("duplicates"):
                _render_duplicate_images(msg["duplicates"])

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

            # 이미지 결과 표시
            images = result.get("images", [])
            if images:
                _render_image_results(images)

            # 이미지 그룹 표시
            groups = result.get("groups")
            if groups:
                _render_image_groups(groups)

            # 중복 이미지 표시
            duplicates = result.get("duplicates")
            if duplicates:
                _render_duplicate_images(duplicates)

        # 어시스턴트 메시지 이력에 추가
        st.session_state["chat_history"].append({
            "role": "assistant",
            "content": result.get("answer", ""),
            "data": data,
            "sql": sql,
            "images": images if images else None,
            "groups": groups,
            "duplicates": duplicates,
            "success": result.get("success", False),
            "intent": result.get("intent"),
            "sql_category": result.get("sql_category"),
            "approval_id": result.get("approval_id"),
        })


def _render_image_results(images: list[dict]):
    """
    이미지 검색 결과를 4열 썸네일 그리드로 표시.

    각 이미지에 썸네일 경로가 있으면 이미지를, 없으면 파일명만 표시합니다.
    유사도 점수가 있으면 캡션에 포함합니다.
    """
    if not images:
        return

    # 그룹/중복 정보만 있는 결과는 별도 렌더러에서 처리
    has_similarity = any("similarity" in img for img in images)
    if not has_similarity:
        return

    cols = st.columns(4)
    for idx, img in enumerate(images):
        with cols[idx % 4]:
            name = img.get("image_name", "unknown")
            similarity = img.get("similarity")
            thumb = img.get("thumbnail_path", "")

            caption = name
            if similarity is not None:
                caption += f" ({similarity:.0%})"

            if thumb and Path(thumb).is_file():
                st.image(thumb, caption=caption, use_container_width=True)
            else:
                st.markdown(f"🖼️ {caption}")


def _render_image_groups(groups: dict):
    """
    클러스터링 그룹 결과를 아코디언(expander) + 썸네일 그리드로 표시.
    """
    if not groups:
        return

    from catalog.catalog import get_image_by_name

    for group_id, members in groups.items():
        with st.expander(f"그룹 {group_id + 1} ({len(members)}장)", expanded=False):
            cols = st.columns(min(4, len(members)))
            for idx, name in enumerate(members):
                with cols[idx % len(cols)]:
                    info = get_image_by_name(name)
                    thumb = info.get("thumbnail_path", "") if info else ""
                    if thumb and Path(thumb).is_file():
                        st.image(thumb, caption=name, use_container_width=True)
                    else:
                        st.markdown(f"🖼️ {name}")


def _render_duplicate_images(duplicates: list[list[str]]):
    """
    중복 이미지 그룹을 경고 배너 + 그룹별 컨테이너로 표시.

    정보 표시만 수행하며 삭제 버튼은 제공하지 않습니다.
    """
    if not duplicates:
        return

    from catalog.catalog import get_image_by_name

    st.info("ℹ️ 아래는 시각적으로 유사한 이미지 그룹입니다. 삭제가 필요하면 데이터 관리 페이지를 이용해 주세요.")

    for idx, group in enumerate(duplicates, 1):
        with st.container(border=True):
            st.markdown(f"**중복 그룹 {idx}** ({len(group)}장)")
            cols = st.columns(min(4, len(group)))
            for j, name in enumerate(group):
                with cols[j % len(cols)]:
                    info = get_image_by_name(name)
                    thumb = info.get("thumbnail_path", "") if info else ""
                    if thumb and Path(thumb).is_file():
                        st.image(thumb, caption=name, use_container_width=True)
                    else:
                        st.markdown(f"🖼️ {name}")
