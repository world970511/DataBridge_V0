"""
DataBridge Streamlit 메인 진입점.

앱 기동 시 startup_checks()로 서비스 상태를 검증한 뒤,
로그인 게이트를 거쳐 사이드바 네비게이션으로 페이지를 전환합니다.

페이지 구성:
    - 💬 Chat: 자연어 데이터 조회/문서 검색 채팅 (모든 사용자)
    - ✅ 승인 관리: SQL 승인 요청 관리 (admin 전용 + 일반 유저 본인 요청 조회)
    - 👥 사용자 관리: 사용자 추가/비활성화 (admin 전용)

실행:
    streamlit run ui/main_ui.py --server.port=8501 --server.address=0.0.0.0
"""

import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 — Streamlit이 스크립트 디렉토리(ui/)만
# path에 넣기 때문에, 프로젝트 루트의 패키지(config, db, auth 등)를 찾으려면 필요
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st

# Streamlit 페이지 설정 — 반드시 다른 st.* 호출보다 먼저 실행
st.set_page_config(
    page_title="DataBridge",
    page_icon="🌉",
    layout="wide",
    initial_sidebar_state="expanded",
)

from config.settings import get_settings
from db.connection import check_connection as check_db
from rag.embedder import check_chroma_connection
from scripts.check_ollama import check_ollama
from watcher.file_watcher import start_watcher
from watcher.classifier import classify_file
from auth.user_manager import ensure_admin_exists
from auth.session import require_login, get_current_user, is_admin, logout
from ui.chat import render_chat_page
from ui.approval_ui import render_approval_page
from ui.admin_ui import render_admin_page
from ui.data_management_ui import render_data_management_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("databridge.ui")


@st.cache_resource
def _run_startup():
    """
    앱 기동 시 1회만 실행되는 초기화 함수.

    PostgreSQL, ChromaDB, Ollama 연결을 순서대로 확인하고,
    ensure_admin_exists()로 관리자 계정을 보장합니다.
    Streamlit의 @st.cache_resource로 서버 리렌더링 시에도 반복 실행되지 않습니다.

    Returns:
        True (정상 기동) — 캐시 키 역할.
    """
    logger.info("=== DataBridge Startup ===")

    settings = get_settings()
    logger.info(f"Watch dir: {settings.watcher.watch_dir}")
    logger.info(f"LLM model: {settings.ollama.model}")

    # 1. PostgreSQL
    logger.info("Checking PostgreSQL...")
    if not check_db():
        logger.error("PostgreSQL connection failed!")
        st.error("PostgreSQL 연결 실패 — 관리자에게 문의하세요.")
        st.stop()
    logger.info("PostgreSQL: OK")

    # 2. ChromaDB
    logger.info("Checking ChromaDB...")
    if not check_chroma_connection():
        logger.error("ChromaDB connection failed!")
        st.error("ChromaDB 연결 실패 — 관리자에게 문의하세요.")
        st.stop()
    logger.info("ChromaDB: OK")

    # 3. Ollama
    logger.info("Checking Ollama...")
    if not check_ollama():
        logger.warning("Ollama check failed — agent features may not work")
    else:
        logger.info("Ollama: OK")

    ensure_admin_exists()
    logger.info("Startup checks completed, admin user ensured")

    # 4. File Watcher — non-blocking 모드로 실행
    #    blocking=False → Observer 스레드만 시작하고 즉시 반환
    observer = start_watcher(watch_dir=settings.watcher.watch_dir, blocking=False)
    logger.info(f"File watcher started on: {settings.watcher.watch_dir}")

    # 5. 기존 파일 초기 스캔 — 이미 마운트된 파일을 처리
    _initial_scan(settings.watcher.watch_dir)

    # 6. 배치 스케줄러 시작 — 등록된 cron 작업 자동 실행
    try:
        from jobs.scheduler import start as start_scheduler
        start_scheduler()
        logger.info("Batch scheduler started")
    except Exception as e:
        logger.warning(f"Batch scheduler start failed: {e}")

    # 7. 외부 DB 자동 연결 — 설정이 enabled이면 시작 시 자동 등록 + 스키마 동기화
    try:
        from db.external.registry import register_from_settings, sync_schema, list_external_dbs
        if register_from_settings():
            ext_dbs = list_external_dbs()
            if ext_dbs:
                sync_schema(ext_dbs[0]["name"])
                logger.info(f"External DB auto-connected: {ext_dbs[0]['name']}")
        # enabled=False이거나 설정 미완료면 조용히 스킵 (register_from_settings 내부에서 로깅)
    except Exception as e:
        logger.warning(f"External DB auto-connect failed (non-critical): {e}")

    return True


def _initial_scan(watch_dir: str):
    """
    앱 시작 시 감시 폴더에 이미 존재하는 파일을 병렬 스캔하여 처리.

    watchdog은 새로 생성/수정되는 파일만 감지하므로,
    Docker 마운트 등으로 사전에 배치된 파일은 별도로 처리해야 합니다.
    classify_file()이 내부적으로 catalog에 UPSERT하므로 중복 처리해도 안전합니다.

    병렬 처리 전략:
        1. 모든 파일을 확장자 기반으로 3개 카테고리로 분류 (빠른 패턴 매칭)
        2. 통계(DB)/문서/이미지 각 카테고리를 별도 스레드에서 동시 처리
        3. 각 로더는 stateless + 커넥션 풀 기반이므로 thread-safe
    """
    import concurrent.futures
    from watcher.classifier import get_file_action

    watch_path = Path(watch_dir)
    if not watch_path.exists():
        logger.warning(f"Watch directory not found for initial scan: {watch_dir}")
        return

    files = [f for f in watch_path.rglob("*") if f.is_file()
             and not f.name.startswith("~$") and not f.name.startswith(".")]

    if not files:
        logger.info("No files found in watch directory for initial scan")
        return

    # ── 파일을 3개 카테고리로 분류 (확장자 패턴 매칭만, 로딩 없음) ──
    db_files = []       # CSV, Excel → PostgreSQL
    doc_files = []      # PDF, DOCX, TXT, HWP 등 → ChromaDB
    image_files = []    # JPG, PNG 등 → DINOv2 + ChromaDB

    for f in files:
        action = get_file_action(str(f))
        if action == "load_to_db":
            db_files.append(f)
        elif action == "register_for_search":
            doc_files.append(f)
        elif action == "register_image":
            image_files.append(f)
        # ignore → 건너뜀

    total = len(db_files) + len(doc_files) + len(image_files)
    logger.info(
        f"Initial scan: {len(files)} files found, {total} to process "
        f"(DB:{len(db_files)}, DOC:{len(doc_files)}, IMG:{len(image_files)})"
    )

    if total == 0:
        return

    def _process_queue(file_list: list, category: str) -> tuple[int, int]:
        """카테고리별 파일 큐를 순차 처리하는 워커 스레드."""
        success, failed = 0, 0
        for fp in file_list:
            try:
                logger.info(f"[{category}] Processing: {fp.name}")
                classify_file(str(fp))
                success += 1
            except Exception:
                failed += 1
                logger.exception(f"[{category}] Error: {fp.name}")
        logger.info(f"[{category}] Done: {success} ok, {failed} failed")
        return success, failed

    # ── 3개 스레드로 병렬 처리 ──
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3, thread_name_prefix="scan"
    ) as executor:
        futures = {}
        if db_files:
            futures["통계/DB"] = executor.submit(_process_queue, db_files, "통계/DB")
        if doc_files:
            futures["문서"] = executor.submit(_process_queue, doc_files, "문서")
        if image_files:
            futures["이미지"] = executor.submit(_process_queue, image_files, "이미지")

        total_success, total_failed = 0, 0
        for name, future in futures.items():
            try:
                s, f = future.result()  # 완료까지 대기 (LLM은 백그라운드로 분리됨)
                total_success += s
                total_failed += f
            except Exception as e:
                logger.error(f"[{name}] Worker failed: {e}")

    logger.info(
        f"Initial scan complete: {total_success} succeeded, {total_failed} failed"
    )


def main():
    """
    Streamlit 메인 렌더링 함수.

    1. _run_startup()으로 서비스 상태 확인 + admin 계정 보장 (1회)
    2. require_login()으로 로그인 강제
    3. 사이드바에 사용자 정보 + 네비게이션 메뉴 표시
    4. 선택된 메뉴에 따라 페이지 렌더링
    """
    _run_startup()

    # 로그인 강제
    require_login()

    # 로그인 성공 후 — 사용자 정보 + 네비게이션
    user = get_current_user()
    if not user:
        st.error("세션 오류가 발생했습니다. 페이지를 새로고침해 주세요.")
        st.stop()

    # 사이드바 구성
    with st.sidebar:
        st.markdown(f"### 🌉 DataBridge")
        st.markdown(f"**{user['display_name']}** ({user['role']})")
        st.markdown("---")

        # 메뉴 구성 — 역할에 따라 다른 메뉴 표시
        menu_options = ["💬 Chat", "📁 데이터 관리"]

        if is_admin():
            menu_options.append("✅ 승인 관리")
            menu_options.append("⚙️ 설정")
        else:
            menu_options.append("📋 내 요청 현황")

        selected = st.radio("메뉴", menu_options, label_visibility="collapsed")

        st.markdown("---")
        if st.button("🚪 로그아웃", use_container_width=True):
            logout()
            st.rerun()

    # 선택된 메뉴에 따라 페이지 렌더링
    if selected == "💬 Chat":
        render_chat_page()
    elif selected == "📁 데이터 관리":
        render_data_management_page()
    elif selected == "✅ 승인 관리":
        render_approval_page()
    elif selected == "⚙️ 설정":
        render_admin_page()
    elif selected == "📋 내 요청 현황":
        render_approval_page()


if __name__ == "__main__":
    main()
