"""
DataBridge 데이터 관리 페이지.

등록된 테이블, 문서, 이미지를 조회하고 삭제 요청을 생성합니다.
삭제는 즉시 실행되지 않고 관리자 승인을 거쳐야 합니다.

의존 모듈:
    - catalog.catalog: list_tables(), list_documents(), list_images()
    - approval.approval_manager: create_delete_request()
    - auth.session: get_current_user()
"""

import logging
from pathlib import Path

import streamlit as st

from catalog.catalog import list_tables, list_documents, list_images
from approval.approval_manager import create_delete_request
from auth.session import get_current_user

logger = logging.getLogger(__name__)


def render_data_management_page():
    """데이터 관리 메인 페이지."""
    st.title("📁 데이터 관리")
    st.caption("등록된 데이터를 조회하고 삭제를 요청할 수 있습니다. 삭제는 관리자 승인 후 실행됩니다.")

    tab1, tab2, tab3 = st.tabs(["📊 테이블", "📄 문서", "🖼️ 이미지"])

    with tab1:
        _render_tables_tab()
    with tab2:
        _render_documents_tab()
    with tab3:
        _render_images_tab()


def _render_tables_tab():
    """등록된 테이블 목록 + 삭제 요청 버튼."""
    tables = list_tables()

    if not tables:
        st.info("등록된 테이블이 없습니다.")
        return

    st.markdown(f"### 등록된 테이블 ({len(tables)}건)")

    for table in tables:
        with st.container(border=True):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**{table['table_name']}**")
                st.caption(
                    f"원본: {table.get('source_file', 'N/A')} | "
                    f"행: {table.get('row_count', 0):,} | "
                    f"컬럼: {table.get('column_count', 0)}"
                )
                if table.get("description"):
                    st.caption(f"설명: {table['description']}")
            with col2:
                if st.button(
                    "🗑 삭제 요청",
                    key=f"del_table_{table['table_name']}",
                    use_container_width=True,
                ):
                    _request_delete(
                        "table",
                        table["table_name"],
                        table.get("source_file", ""),
                        {
                            "row_count": table.get("row_count", 0),
                            "column_count": table.get("column_count", 0),
                        },
                    )


def _render_documents_tab():
    """등록된 문서 목록 + 삭제 요청 버튼."""
    documents = list_documents()

    if not documents:
        st.info("등록된 문서가 없습니다.")
        return

    st.markdown(f"### 등록된 문서 ({len(documents)}건)")

    for doc in documents:
        with st.container(border=True):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**{doc['doc_name']}**")
                st.caption(
                    f"원본: {doc.get('source_file', 'N/A')} | "
                    f"유형: {doc.get('file_type', 'N/A')} | "
                    f"청크: {doc.get('chunk_count', 0)}"
                )
            with col2:
                if st.button(
                    "🗑 삭제 요청",
                    key=f"del_doc_{doc['doc_name']}",
                    use_container_width=True,
                ):
                    _request_delete(
                        "document",
                        doc["doc_name"],
                        doc.get("source_file", ""),
                        {"chunk_count": doc.get("chunk_count", 0)},
                    )


def _render_images_tab():
    """등록된 이미지 목록 + 삭제 요청 버튼 + 썸네일 표시."""
    images = list_images()

    if not images:
        st.info("등록된 이미지가 없습니다.")
        return

    st.markdown(f"### 등록된 이미지 ({len(images)}건)")

    # 4열 그리드
    cols_per_row = 4
    for row_start in range(0, len(images), cols_per_row):
        row_images = images[row_start:row_start + cols_per_row]
        cols = st.columns(cols_per_row)

        for idx, img in enumerate(row_images):
            with cols[idx]:
                thumb = img.get("thumbnail_path")
                if thumb and Path(thumb).exists():
                    st.image(thumb, use_container_width=True)
                else:
                    st.markdown("🖼️ *No thumbnail*")

                st.caption(f"**{img['image_name']}**")
                size_info = ""
                if img.get("width") and img.get("height"):
                    size_info = f"{img['width']}x{img['height']}"
                if img.get("camera_model"):
                    size_info += f" | {img['camera_model']}"
                if size_info:
                    st.caption(size_info)

                if st.button(
                    "🗑 삭제",
                    key=f"del_img_{img['image_name']}",
                    use_container_width=True,
                ):
                    _request_delete(
                        "image",
                        img["image_name"],
                        img.get("source_file", ""),
                        {"thumbnail_path": img.get("thumbnail_path", "")},
                    )


def _request_delete(
    resource_type: str,
    resource_name: str,
    source_file: str,
    details: dict,
):
    """삭제 승인 요청을 생성하고 사용자에게 알림."""
    user = get_current_user()
    if not user:
        st.error("로그인이 필요합니다.")
        return

    req_id = create_delete_request(
        resource_type=resource_type,
        resource_name=resource_name,
        source_file=source_file,
        requested_by=user["username"],
        details=details,
    )
    if req_id:
        st.success(f"삭제 요청이 생성되었습니다 (요청 #{req_id}). 관리자 승인을 기다려주세요.")
    else:
        st.error("삭제 요청 생성에 실패했습니다.")
