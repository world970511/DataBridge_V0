"""
DataBridge 관리자 페이지.

관리자가 사용자 관리, 감사 로그 조회, 시스템 상태를 확인할 수 있는 페이지입니다.
일반 사용자는 이 페이지에 접근할 수 없습니다.

기능:
    - 사용자 관리: 등록된 사용자 목록 조회, 추가, 비활성화
    - 감사 로그: 시스템 활동 이력 조회
    - 시스템 상태: DB, ChromaDB, Ollama 연결 상태 및 통계

의존 모듈:
    - auth.user_manager: create_user(), list_users(), delete_user()
    - auth.session: is_admin()
    - db.connection: execute_query(), check_connection()
    - rag.embedder: check_chroma_connection()
    - catalog.catalog: get_catalog_summary()
"""

import logging
from datetime import datetime, timedelta

import streamlit as st

from auth.user_manager import create_user, list_users, delete_user
from auth.session import is_admin

logger = logging.getLogger(__name__)


def render_admin_page():
    """
    관리자 페이지를 렌더링.

    admin 권한이 없으면 접근 차단 메시지를 표시합니다.
    admin이면 탭으로 구분된 관리 기능을 표시합니다:
    - 사용자 관리
    - 감사 로그
    - 시스템 상태
    - LLM 설정
    """
    if not is_admin():
        st.error("관리자만 접근할 수 있습니다.")
        st.stop()

    st.title("관리자 페이지")

    # 탭 구성
    tab_users, tab_audit, tab_system, tab_llm = st.tabs([
        "사용자 관리",
        "감사 로그",
        "시스템 상태",
        "LLM 설정",
    ])

    with tab_users:
        _render_users_tab()

    with tab_audit:
        _render_audit_tab()

    with tab_system:
        _render_system_tab()

    with tab_llm:
        _render_llm_settings_tab()


def _render_users_tab():
    """사용자 관리 탭."""
    st.caption("사용자를 추가하거나 비활성화합니다.")

    # 두 컬럼 레이아웃: 좌=사용자 목록, 우=추가 폼
    col_list, col_add = st.columns([2, 1])

    with col_list:
        _render_user_list()

    with col_add:
        _render_add_user_form()


def _render_user_list():
    """등록된 사용자 목록을 테이블로 표시하고 비활성화 버튼을 제공."""
    st.markdown("### 사용자 목록")

    # 비활성 계정도 표시 옵션
    show_inactive = st.checkbox("비활성 계정도 표시", value=False)

    users = list_users(include_inactive=show_inactive)

    if not users:
        st.info("등록된 사용자가 없습니다.")
        return

    for user in users:
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])

            with col1:
                status_icon = "🟢" if user.get("is_active") else "🔴"
                st.markdown(
                    f"{status_icon} **{user['username']}** "
                    f"({user.get('display_name', '')})"
                )

            with col2:
                role = user.get("role", "user")
                role_badge = "👑 admin" if role == "admin" else "👤 user"
                st.caption(role_badge)

            with col3:
                created = user.get("created_at")
                if created:
                    st.caption(str(created)[:10])

            with col4:
                # admin 계정은 비활성화 버튼 비표시
                if user.get("role") != "admin" and user.get("is_active"):
                    if st.button(
                        "비활성화",
                        key=f"deactivate_{user['id']}",
                        type="secondary",
                    ):
                        if delete_user(user["username"]):
                            st.success(f"{user['username']} 비활성화됨")
                            st.rerun()
                        else:
                            st.error("비활성화 실패")


def _render_add_user_form():
    """새 사용자 추가 폼."""
    st.markdown("### 사용자 추가")

    with st.form("add_user_form"):
        username = st.text_input("사용자명 (로그인 ID)", placeholder="예: kim")
        display_name = st.text_input("표시 이름", placeholder="예: 김철수")
        password = st.text_input("비밀번호", type="password")
        password_confirm = st.text_input("비밀번호 확인", type="password")
        role = st.selectbox("역할", ["user", "admin"])

        submitted = st.form_submit_button("➕ 사용자 추가", use_container_width=True)

    if submitted:
        # 입력 검증
        if not username or not password:
            st.error("사용자명과 비밀번호는 필수입니다.")
            return

        if len(username) < 2:
            st.error("사용자명은 2자 이상이어야 합니다.")
            return

        if len(password) < 4:
            st.error("비밀번호는 4자 이상이어야 합니다.")
            return

        if password != password_confirm:
            st.error("비밀번호가 일치하지 않습니다.")
            return

        # 사용자 생성
        result = create_user(
            username=username,
            password=password,
            role=role,
            display_name=display_name or username,
        )

        if result:
            st.success(f"사용자 '{username}' 생성 완료!")
            st.rerun()
        else:
            st.error(f"사용자 생성 실패 - 이미 존재하는 사용자명일 수 있습니다.")


def _render_audit_tab():
    """감사 로그 탭."""
    from db.connection import execute_query

    st.caption("시스템 활동 이력을 조회합니다.")

    # 필터 옵션
    col1, col2, col3 = st.columns(3)

    with col1:
        # 기간 필터
        period = st.selectbox(
            "기간",
            ["최근 1시간", "최근 24시간", "최근 7일", "전체"],
            index=1,
        )

    with col2:
        # 액션 타입 필터
        action_filter = st.selectbox(
            "액션 타입",
            ["전체", "query", "sql_generate", "sql_execute", "approval", "error"],
            index=0,
        )

    with col3:
        # 사용자 필터
        user_filter = st.text_input("사용자 ID", placeholder="모든 사용자")

    # 기간 계산
    now = datetime.now()
    if period == "최근 1시간":
        since = now - timedelta(hours=1)
    elif period == "최근 24시간":
        since = now - timedelta(days=1)
    elif period == "최근 7일":
        since = now - timedelta(days=7)
    else:
        since = None

    # 쿼리 구성
    conditions = []
    params = []

    if since:
        conditions.append("created_at >= %s")
        params.append(since)

    if action_filter != "전체":
        conditions.append("action_type = %s")
        params.append(action_filter)

    if user_filter:
        conditions.append("user_id = %s")
        params.append(user_filter)

    where_clause = " AND ".join(conditions) if conditions else "TRUE"

    # 로그 조회
    try:
        logs = execute_query(
            f"""
            SELECT id, action_type, user_id, query_text, sql_generated,
                   result_summary, status, created_at
            FROM audit_log
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT 100
            """,
            tuple(params) if params else None,
        )

        if not logs:
            st.info("해당 조건의 로그가 없습니다.")
            return

        st.markdown(f"**{len(logs)}건** (최대 100건 표시)")

        for log in logs:
            with st.expander(
                f"[{log['action_type']}] {log.get('user_id', 'system')} - "
                f"{str(log['created_at'])[:19]}"
            ):
                col_a, col_b = st.columns(2)

                with col_a:
                    st.markdown("**상태:**")
                    status = log.get("status", "")
                    if status == "success":
                        st.success(status)
                    elif status == "error":
                        st.error(status)
                    else:
                        st.info(status or "N/A")

                with col_b:
                    st.markdown("**사용자:**")
                    st.text(log.get("user_id") or "system")

                if log.get("query_text"):
                    st.markdown("**질의:**")
                    st.text(log["query_text"][:500])

                if log.get("sql_generated"):
                    st.markdown("**생성된 SQL:**")
                    st.code(log["sql_generated"], language="sql")

                if log.get("result_summary"):
                    st.markdown("**결과 요약:**")
                    st.text(log["result_summary"][:300])

    except Exception as e:
        st.error(f"로그 조회 실패: {e}")


def _render_system_tab():
    """시스템 상태 탭."""
    from db.connection import check_connection
    from rag.embedder import check_chroma_connection
    from catalog.catalog import get_catalog_summary

    st.caption("서비스 연결 상태 및 시스템 통계를 확인합니다.")

    # 새로고침 버튼
    if st.button("상태 새로고침"):
        st.rerun()

    st.markdown("### 서비스 상태")

    col1, col2, col3 = st.columns(3)

    # PostgreSQL 상태
    with col1:
        with st.container(border=True):
            st.markdown("**PostgreSQL**")
            try:
                if check_connection():
                    st.success("연결됨")
                else:
                    st.error("연결 실패")
            except Exception as e:
                st.error(f"오류: {e}")

    # ChromaDB 상태
    with col2:
        with st.container(border=True):
            st.markdown("**ChromaDB**")
            try:
                if check_chroma_connection():
                    st.success("연결됨")
                else:
                    st.error("연결 실패")
            except Exception as e:
                st.error(f"오류: {e}")

    # Ollama 상태
    with col3:
        with st.container(border=True):
            st.markdown("**Ollama**")
            try:
                import requests
                from config.settings import get_settings

                settings = get_settings()
                resp = requests.get(
                    f"{settings.ollama.host}/api/tags",
                    timeout=5,
                )
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    st.success(f"연결됨 ({len(models)} 모델)")
                else:
                    st.warning(f"HTTP {resp.status_code}")
            except requests.ConnectionError:
                st.error("연결 실패")
            except Exception as e:
                st.error(f"오류: {e}")

    st.markdown("### 데이터 통계")

    try:
        summary = get_catalog_summary()

        col_a, col_b = st.columns(2)

        with col_a:
            st.metric(
                label="등록된 테이블",
                value=summary.get("total_tables", 0),
            )

        with col_b:
            st.metric(
                label="등록된 문서",
                value=summary.get("total_documents", 0),
            )

    except Exception as e:
        st.error(f"통계 조회 실패: {e}")

    st.markdown("### 최근 활동")

    try:
        from db.connection import execute_query

        recent = execute_query(
            """
            SELECT action_type, COUNT(*) as cnt
            FROM audit_log
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY action_type
            ORDER BY cnt DESC
            """
        )

        if recent:
            for row in recent:
                st.text(f"  {row['action_type']}: {row['cnt']}건")
        else:
            st.text("  최근 24시간 활동 없음")

    except Exception as e:
        st.error(f"활동 조회 실패: {e}")


def _render_llm_settings_tab():
    """LLM 설정 탭."""
    st.caption("오케스트레이터와 에이전트의 LLM 모델을 설정합니다.")

    # 네트워크 상태 확인
    _render_network_status()

    st.markdown("---")

    # 현재 설정 로드
    try:
        from config.llm_settings import get_llm_settings, save_all_llm_settings
        current_settings = get_llm_settings()
    except Exception as e:
        st.error(f"설정 로드 실패: {e}")
        current_settings = {}

    # 폐쇄망 모드 토글
    st.markdown("### 환경 설정")
    airgapped = st.checkbox(
        "폐쇄망 모드",
        value=current_settings.get("airgapped_mode", "false").lower() == "true",
        help="활성화하면 상용 API(OpenAI, Anthropic)를 사용할 수 없으며, Ollama만 사용 가능합니다.",
    )

    st.markdown("---")

    # 두 컬럼 레이아웃
    col_orch, col_agent = st.columns(2)

    with col_orch:
        st.markdown("### 오케스트레이터 설정")
        st.caption("의도 분류 등 간단한 작업에 사용됩니다. 상용 모델 사용 시 빠르고 정확한 분류가 가능합니다.")

        orch_provider = st.selectbox(
            "프로바이더",
            options=["ollama", "openai", "anthropic"],
            index=["ollama", "openai", "anthropic"].index(
                current_settings.get("orchestrator_provider", "ollama")
            ),
            key="orch_provider",
            disabled=airgapped,
        )

        orch_model = st.text_input(
            "모델명",
            value=current_settings.get("orchestrator_model", "exaone3.5:7.8b"),
            key="orch_model",
            help="Ollama: exaone3.5:7.8b | OpenAI: gpt-4o-mini | Anthropic: claude-3-5-haiku-20241022",
        )

        orch_base_url = st.text_input(
            "API URL",
            value=current_settings.get("orchestrator_base_url", "http://host.docker.internal:11434"),
            key="orch_base_url",
            help="Ollama 서버 URL 또는 OpenAI 호환 엔드포인트",
        )

        orch_api_key = st.text_input(
            "API 키",
            value=current_settings.get("orchestrator_api_key", ""),
            type="password",
            key="orch_api_key",
            help="OpenAI/Anthropic API 키 (Ollama는 불필요)",
            disabled=orch_provider == "ollama",
        )

        # 연결 테스트 버튼
        if st.button("연결 테스트", key="test_orch"):
            _test_llm_connection(orch_provider, orch_base_url, orch_api_key, "오케스트레이터")

    with col_agent:
        st.markdown("### 에이전트 설정")
        st.caption("SQL 생성, RAG 등 데이터 처리에 사용됩니다. 민감 데이터 보호를 위해 로컬 모델을 권장합니다.")

        agent_provider = st.selectbox(
            "프로바이더",
            options=["ollama", "openai", "anthropic"],
            index=["ollama", "openai", "anthropic"].index(
                current_settings.get("agent_provider", "ollama")
            ),
            key="agent_provider",
            disabled=airgapped,
        )

        agent_model = st.text_input(
            "모델명",
            value=current_settings.get("agent_model", "exaone3.5:7.8b"),
            key="agent_model",
            help="Ollama: exaone3.5:7.8b | OpenAI: gpt-4o | Anthropic: claude-3-5-sonnet-20241022",
        )

        agent_base_url = st.text_input(
            "API URL",
            value=current_settings.get("agent_base_url", "http://localhost:11434"),
            key="agent_base_url",
            help="Ollama 서버 URL 또는 OpenAI 호환 엔드포인트",
        )

        agent_api_key = st.text_input(
            "API 키",
            value=current_settings.get("agent_api_key", ""),
            type="password",
            key="agent_api_key",
            help="OpenAI/Anthropic API 키 (Ollama는 불필요)",
            disabled=agent_provider == "ollama",
        )

        # 연결 테스트 버튼
        if st.button("연결 테스트", key="test_agent"):
            _test_llm_connection(agent_provider, agent_base_url, agent_api_key, "에이전트")

    st.markdown("---")

    # 저장 버튼
    if st.button("설정 저장", type="primary", use_container_width=True):
        new_settings = {
            "orchestrator_provider": "ollama" if airgapped else orch_provider,
            "orchestrator_model": orch_model,
            "orchestrator_base_url": orch_base_url,
            "orchestrator_api_key": "" if airgapped else orch_api_key,
            "agent_provider": "ollama" if airgapped else agent_provider,
            "agent_model": agent_model,
            "agent_base_url": agent_base_url,
            "agent_api_key": "" if airgapped else agent_api_key,
            "airgapped_mode": "true" if airgapped else "false",
        }

        try:
            from auth.session import get_current_user
            user = get_current_user()
            username = user.get("username", "admin") if user else "admin"

            if save_all_llm_settings(new_settings, updated_by=username):
                st.success("설정이 저장되었습니다. 다음 LLM 호출부터 적용됩니다.")
            else:
                st.error("일부 설정 저장에 실패했습니다.")
        except Exception as e:
            st.error(f"설정 저장 실패: {e}")

    # 설정 설명
    with st.expander("설정 가이드"):
        st.markdown("""
**환경별 권장 설정:**

| 환경 | 오케스트레이터 | 에이전트 |
|------|---------------|----------|
| **완전 폐쇄망** | Ollama | Ollama |
| **제한적 인터넷** | OpenAI/Anthropic | Ollama (권장) |
| **일반 환경** | OpenAI/Anthropic | Ollama 또는 상용 |

**프로바이더별 모델 예시:**

- **Ollama**: `exaone3.5:7.8b`, `llama3.1:8b`, `qwen2.5:7b`
- **OpenAI**: `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`
- **Anthropic**: `claude-3-5-sonnet-20241022`, `claude-3-5-haiku-20241022`

**보안 고려사항:**
- 에이전트는 스키마 정보와 쿼리 결과를 LLM에 전달합니다
- 민감 데이터 보호를 위해 에이전트는 로컬 모델(Ollama) 사용을 권장합니다
- 오케스트레이터는 질의 텍스트만 전달하므로 상용 모델 사용 시 위험이 낮습니다
        """)


def _render_network_status():
    """네트워크 연결 상태를 표시."""
    st.markdown("### 네트워크 상태")

    try:
        from agent._llm import check_network_connectivity
        status = check_network_connectivity()

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            if status["internet_available"]:
                st.success("인터넷: 연결됨")
            else:
                st.error("인터넷: 연결 안됨")

        with col2:
            if status["openai_reachable"]:
                st.success("OpenAI: 접근 가능")
            else:
                st.warning("OpenAI: 접근 불가")

        with col3:
            if status["anthropic_reachable"]:
                st.success("Anthropic: 접근 가능")
            else:
                st.warning("Anthropic: 접근 불가")

        with col4:
            mode = status["recommended_mode"]
            if mode == "airgapped":
                st.info("권장: 폐쇄망 모드")
            else:
                st.info("권장: 하이브리드 모드")

    except Exception as e:
        st.warning(f"네트워크 상태 확인 실패: {e}")


def _test_llm_connection(provider: str, base_url: str, api_key: str, name: str):
    """LLM 연결 테스트를 수행하고 결과를 표시."""
    try:
        from config.settings import LLMProviderConfig
        from agent._llm import check_provider_connection

        config = LLMProviderConfig(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
        )

        with st.spinner(f"{name} 연결 테스트 중..."):
            result = check_provider_connection(provider, config)

        if result["connected"]:
            st.success(f"{name}: {result['message']}")
            if result.get("models"):
                st.caption(f"사용 가능한 모델: {', '.join(result['models'][:5])}")
        else:
            st.error(f"{name}: {result['message']}")

    except Exception as e:
        st.error(f"{name} 연결 테스트 실패: {e}")
