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
    tab_users, tab_audit, tab_jobs, tab_noti, tab_system, tab_llm = st.tabs([
        "사용자 관리",
        "감사 로그",
        "배치 작업",
        "알림 설정",
        "시스템 상태",
        "LLM 설정",
    ])

    with tab_users:
        _render_users_tab()

    with tab_audit:
        _render_audit_tab()

    with tab_jobs:
        _render_jobs_tab()

    with tab_noti:
        _render_notification_tab()

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


def _render_jobs_tab():
    """배치 작업 관리 탭."""
    from agent.tools.manage_jobs import (
        list_jobs, create_job, toggle_job, delete_job, run_job,
        get_job_history, get_recent_history,
    )
    from jobs.scheduler import is_running as scheduler_is_running, start as scheduler_start, stop as scheduler_stop
    from auth.session import get_current_user

    st.caption("배치 작업의 등록·실행·이력을 관리합니다.")

    # ── 스케줄러 상태 ──
    with st.container(border=True):
        col_status, col_action = st.columns([3, 1])
        with col_status:
            if scheduler_is_running():
                st.success("🟢 스케줄러 실행 중 — 활성 작업이 cron 일정에 따라 자동 실행됩니다.")
            else:
                st.warning("🔴 스케줄러 중지됨 — 수동 실행만 가능합니다.")
        with col_action:
            if scheduler_is_running():
                if st.button("⏹ 스케줄러 중지", use_container_width=True):
                    scheduler_stop()
                    st.rerun()
            else:
                if st.button("▶️ 스케줄러 시작", use_container_width=True):
                    scheduler_start()
                    st.rerun()

    st.markdown("---")

    # ── 작업 목록 + 신규 등록 ──
    col_list, col_add = st.columns([3, 1])

    with col_add:
        st.markdown("### ➕ 작업 등록")
        with st.form("add_job_form", clear_on_submit=True):
            job_name = st.text_input("작업명", placeholder="예: daily_sales_refresh")
            job_desc = st.text_input("설명", placeholder="예: 일별 매출 마트 갱신")
            job_sql = st.text_area("SQL", height=120, placeholder="CREATE TABLE mart_... AS SELECT ...")
            job_cron = st.text_input("Cron 표현식", placeholder="0 7 * * *")
            st.caption("분 시 일 월 요일 (예: `0 7 * * *` = 매일 07:00)")
            submitted = st.form_submit_button("등록", use_container_width=True)

        if submitted:
            if not job_name or not job_sql or not job_cron:
                st.error("작업명, SQL, Cron은 필수입니다.")
            else:
                user = get_current_user()
                username = user.get("username", "admin") if user else "admin"
                new_id = create_job(
                    job_name=job_name.strip(),
                    description=job_desc,
                    sql_text=job_sql,
                    cron_expr=job_cron.strip(),
                    created_by=username,
                )
                if new_id:
                    st.success(f"작업 등록 완료 (ID: {new_id})")
                    st.rerun()
                else:
                    st.error("작업 등록 실패 — 중복된 이름이거나 Cron 형식이 잘못되었습니다.")

    with col_list:
        st.markdown("### 등록된 작업")

        show_inactive = st.checkbox("비활성 작업도 표시", value=True, key="jobs_show_inactive")
        jobs = list_jobs(active_only=not show_inactive)

        if not jobs:
            st.info("등록된 배치 작업이 없습니다.")
        else:
            for job in jobs:
                jid = job["id"]
                active = job["is_active"]
                status_icon = "🟢" if active else "⏸️"
                last_status = job.get("last_status") or "—"
                last_run = str(job.get("last_run_at") or "—")[:19]

                with st.expander(
                    f"{status_icon} **{job['job_name']}** | "
                    f"최근: {last_status} ({last_run}) | "
                    f"cron: `{job['cron_expr']}`"
                ):
                    # 작업 상세
                    st.markdown(f"**설명:** {job.get('description') or '—'}")
                    st.code(job["sql_text"], language="sql")
                    st.caption(
                        f"생성자: {job.get('created_by', '—')} | "
                        f"생성일: {str(job.get('created_at', ''))[:19]}"
                    )

                    # 액션 버튼
                    btn_cols = st.columns(4)
                    with btn_cols[0]:
                        if active:
                            if st.button("⏸ 비활성화", key=f"toggle_{jid}"):
                                toggle_job(jid, False)
                                st.rerun()
                        else:
                            if st.button("▶ 활성화", key=f"toggle_{jid}"):
                                toggle_job(jid, True)
                                st.rerun()

                    with btn_cols[1]:
                        if st.button("🔄 즉시 실행", key=f"run_{jid}"):
                            with st.spinner("실행 중..."):
                                result = run_job(jid)
                            if result["success"]:
                                st.success(result["message"])
                            else:
                                st.error(result["message"])

                    with btn_cols[2]:
                        if st.button("📜 실행 이력", key=f"history_{jid}"):
                            st.session_state[f"show_history_{jid}"] = not st.session_state.get(f"show_history_{jid}", False)

                    with btn_cols[3]:
                        if st.button("🗑 삭제", key=f"delete_{jid}", type="secondary"):
                            if delete_job(jid):
                                st.success("작업 삭제됨")
                                st.rerun()
                            else:
                                st.error("삭제 실패")

                    # 실행 이력 표시
                    if st.session_state.get(f"show_history_{jid}", False):
                        history = get_job_history(jid, limit=10)
                        if history:
                            st.markdown("**최근 실행 이력:**")
                            for h in history:
                                h_status = h.get("status", "")
                                h_icon = "✅" if h_status == "success" else "❌" if h_status == "failed" else "⏳"
                                h_time = str(h.get("started_at", ""))[:19]
                                h_elapsed = h.get("execution_time")
                                h_elapsed_str = f"{h_elapsed:.1f}초" if h_elapsed else "—"
                                h_rows = h.get("rows_affected", 0)
                                h_err = h.get("error_message") or ""

                                line = f"{h_icon} {h_time} | {h_status} | {h_rows}행 | {h_elapsed_str}"
                                if h_err:
                                    st.markdown(line)
                                    st.caption(f"⚠ {h_err[:200]}")
                                else:
                                    st.markdown(line)
                        else:
                            st.info("실행 이력이 없습니다.")

    # ── 전체 최근 이력 ──
    st.markdown("---")
    st.markdown("### 📊 전체 최근 실행 이력")
    recent = get_recent_history(limit=20)
    if recent:
        import pandas as pd
        df = pd.DataFrame(recent)
        display_cols = ["job_name", "status", "started_at", "rows_affected", "execution_time", "error_message"]
        existing_cols = [c for c in display_cols if c in df.columns]
        if existing_cols:
            df_display = df[existing_cols].copy()
            col_rename = {
                "job_name": "작업명",
                "status": "상태",
                "started_at": "실행 시각",
                "rows_affected": "영향 행수",
                "execution_time": "소요(초)",
                "error_message": "에러",
            }
            df_display.rename(columns={k: v for k, v in col_rename.items() if k in df_display.columns}, inplace=True)
            st.dataframe(df_display, use_container_width=True, hide_index=True)
    else:
        st.info("실행 이력이 없습니다.")


def _render_notification_tab():
    """알림 설정 탭 — 구독 관리 + 발송 이력."""
    from db.connection import execute_query, execute_command
    from notifications.events import EVENT_PATTERNS, CHANNELS

    st.caption("이벤트 발생 시 Webhook/Slack/Teams로 알림을 전송합니다.")

    # ── 구독 목록 + 추가 폼 ──
    col_list, col_add = st.columns([3, 1])

    with col_add:
        st.markdown("### 구독 추가")
        with st.form("add_noti_form", clear_on_submit=True):
            display_name = st.text_input("별명", placeholder="예: 슬랙-파일알림")
            event_pattern = st.selectbox("이벤트 패턴", EVENT_PATTERNS)
            custom_pattern = st.text_input(
                "직접 입력 (위 선택 대신)", placeholder="예: file.*"
            )
            channel = st.selectbox("채널", CHANNELS)
            target = st.text_input("대상 URL", placeholder="https://hooks.slack.com/...")
            secret = st.text_input("Secret (Webhook HMAC용)", type="password")
            submitted = st.form_submit_button("등록", use_container_width=True)

        if submitted:
            pattern = custom_pattern.strip() if custom_pattern.strip() else event_pattern
            if not target.strip():
                st.error("대상 URL은 필수입니다.")
            else:
                try:
                    execute_command(
                        """
                        INSERT INTO notification_subscriptions
                            (event_pattern, channel, target, secret, display_name, created_by)
                        VALUES (%s, %s, %s, %s, %s, 'admin')
                        """,
                        (pattern, channel, target.strip(), secret or None, display_name or None),
                    )
                    # 캐시 무효화
                    from notifications.dispatcher import invalidate_cache
                    invalidate_cache()
                    st.success(f"구독 등록 완료: {pattern} → {channel}")
                    st.rerun()
                except Exception as e:
                    st.error(f"등록 실패: {e}")

    with col_list:
        st.markdown("### 등록된 구독")

        try:
            subs = execute_query(
                """
                SELECT id, event_pattern, channel, target, secret,
                       display_name, enabled, created_at
                FROM notification_subscriptions
                ORDER BY created_at DESC
                """
            )
        except Exception as e:
            st.error(f"구독 조회 실패: {e}")
            subs = []

        if not subs:
            st.info("등록된 알림 구독이 없습니다. 오른쪽 폼에서 추가하세요.")
        else:
            for sub in subs:
                sid = sub["id"]
                enabled = sub["enabled"]
                status_icon = "🔔" if enabled else "🔕"
                name_part = f" ({sub['display_name']})" if sub.get("display_name") else ""
                target_short = sub["target"][:50] + ("..." if len(sub["target"]) > 50 else "")

                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])

                    with c1:
                        st.markdown(
                            f"{status_icon} **{sub['event_pattern']}** → "
                            f"`{sub['channel']}`{name_part}"
                        )
                        st.caption(target_short)

                    with c2:
                        if enabled:
                            if st.button("비활성화", key=f"noti_off_{sid}"):
                                execute_command(
                                    "UPDATE notification_subscriptions SET enabled = FALSE, updated_at = NOW() WHERE id = %s",
                                    (sid,),
                                )
                                from notifications.dispatcher import invalidate_cache
                                invalidate_cache()
                                st.rerun()
                        else:
                            if st.button("활성화", key=f"noti_on_{sid}"):
                                execute_command(
                                    "UPDATE notification_subscriptions SET enabled = TRUE, updated_at = NOW() WHERE id = %s",
                                    (sid,),
                                )
                                from notifications.dispatcher import invalidate_cache
                                invalidate_cache()
                                st.rerun()

                    with c3:
                        if st.button("테스트", key=f"noti_test_{sid}"):
                            _test_notification(sub)

                    with c4:
                        if st.button("삭제", key=f"noti_del_{sid}", type="secondary"):
                            execute_command(
                                "DELETE FROM notification_subscriptions WHERE id = %s",
                                (sid,),
                            )
                            from notifications.dispatcher import invalidate_cache
                            invalidate_cache()
                            st.rerun()

    # ── 발송 이력 ──
    st.markdown("---")
    st.markdown("### 발송 이력")

    col_filter1, col_filter2 = st.columns(2)
    with col_filter1:
        log_status_filter = st.selectbox(
            "상태", ["전체", "success", "failed"], key="noti_log_status"
        )
    with col_filter2:
        log_limit = st.selectbox("표시 건수", [20, 50, 100], key="noti_log_limit")

    try:
        status_cond = ""
        params = []
        if log_status_filter != "전체":
            status_cond = "WHERE status = %s"
            params.append(log_status_filter)

        logs = execute_query(
            f"""
            SELECT id, event_type, channel, target, status,
                   error_message, response_code, elapsed_ms, created_at
            FROM notification_log
            {status_cond}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            tuple(params + [log_limit]),
        )

        if not logs:
            st.info("발송 이력이 없습니다.")
        else:
            import pandas as pd

            df = pd.DataFrame(logs)
            display_cols = [
                "event_type", "channel", "status",
                "response_code", "elapsed_ms", "error_message", "created_at",
            ]
            existing = [c for c in display_cols if c in df.columns]
            df_display = df[existing].copy()
            col_rename = {
                "event_type": "이벤트",
                "channel": "채널",
                "status": "상태",
                "response_code": "HTTP",
                "elapsed_ms": "소요(ms)",
                "error_message": "에러",
                "created_at": "발송 시각",
            }
            df_display.rename(
                columns={k: v for k, v in col_rename.items() if k in df_display.columns},
                inplace=True,
            )
            st.dataframe(df_display, use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"발송 이력 조회 실패: {e}")


def _test_notification(subscription: dict):
    """테스트 알림 전송."""
    try:
        from notifications.senders import get_sender
        from config.settings import get_settings

        sender = get_sender(subscription["channel"])
        test_payload = {
            "message": "DataBridge 알림 테스트입니다.",
            "subscription_id": subscription["id"],
            "test": True,
        }

        result = sender.send(
            target=subscription["target"],
            event_type="test.ping",
            payload=test_payload,
            secret=subscription.get("secret"),
        )

        if result["success"]:
            st.success(f"테스트 성공 (HTTP {result.get('status_code', '-')})")
        else:
            st.error(f"테스트 실패: {result.get('error', '알 수 없는 오류')}")

    except Exception as e:
        st.error(f"테스트 발송 실패: {e}")


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

    # ── 외부 DB 상태 ──
    _render_external_db_status()

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


def _render_external_db_status():
    """외부 DB 연결 상태 섹션."""
    from config.settings import get_settings

    settings = get_settings()
    if not settings.external_db.enabled:
        return  # 비활성이면 섹션 자체를 숨김

    st.markdown("### 외부 DB")

    try:
        from db.external.registry import (
            list_external_dbs, sync_schema, register_from_settings,
        )

        ext_dbs = list_external_dbs()

        if not ext_dbs:
            # 설정은 있지만 아직 등록 안 됨 → 등록 시도
            with st.container(border=True):
                st.info(
                    f"외부 DB가 설정되어 있지만 아직 연결되지 않았습니다. "
                    f"({settings.external_db.name} @ {settings.external_db.host}:{settings.external_db.port})"
                )
                if st.button("외부 DB 연결"):
                    with st.spinner("연결 중..."):
                        ok = register_from_settings()
                    if ok:
                        st.success("연결 성공!")
                        st.rerun()
                    else:
                        st.error("연결 실패 — 호스트/포트/인증 정보를 확인하세요.")
            return

        for db_info in ext_dbs:
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 1, 1])

                with c1:
                    status_icon = "🟢" if db_info["connected"] else "🔴"
                    st.markdown(
                        f"{status_icon} **{db_info['name']}** "
                        f"(`{db_info['db_type']}`)"
                    )

                with c2:
                    if st.button("스키마 동기화", key=f"sync_{db_info['name']}"):
                        with st.spinner("동기화 중..."):
                            count = sync_schema(db_info["name"])
                        if count > 0:
                            st.success(f"{count}개 테이블 동기화 완료")
                            st.rerun()
                        else:
                            st.warning("동기화할 테이블이 없습니다.")

                with c3:
                    if st.button("연결 해제", key=f"remove_{db_info['name']}"):
                        from db.external.registry import remove_external_db
                        remove_external_db(db_info["name"])
                        st.rerun()

        # 동기화된 외부 테이블 목록
        try:
            from db.connection import execute_query
            ext_tables = execute_query(
                """
                SELECT table_name, row_count, column_count, description
                FROM catalog_tables
                WHERE file_type = 'external_db'
                ORDER BY table_name
                """
            )
            if ext_tables:
                with st.expander(f"외부 테이블 ({len(ext_tables)}개)"):
                    for t in ext_tables:
                        rows_str = f"{t['row_count']:,}" if t.get("row_count") else "?"
                        cols_str = str(t.get("column_count", "?"))
                        st.caption(
                            f"**{t['table_name']}** — "
                            f"{rows_str}행, {cols_str}열 | "
                            f"{t.get('description', '')}"
                        )
        except Exception:
            pass

    except ImportError:
        st.warning("외부 DB 모듈을 로드할 수 없습니다.")
    except Exception as e:
        st.error(f"외부 DB 상태 조회 실패: {e}")


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

    # 환경 설정 섹션: 폐쇄망 + Ollama 실행 환경
    st.markdown("### 환경 설정")

    _render_ollama_compute_status()

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

        _orch_providers = ["ollama", "openai", "anthropic", "huggingface"]
        _orch_current = current_settings.get("orchestrator_provider", "ollama")
        if _orch_current not in _orch_providers:
            _orch_current = "ollama"
        orch_provider = st.selectbox(
            "프로바이더",
            options=_orch_providers,
            index=_orch_providers.index(_orch_current),
            key="orch_provider",
            disabled=airgapped,
        )

        orch_model = st.text_input(
            "모델명",
            value=current_settings.get("orchestrator_model", ""),
            key="orch_model",
            help="Ollama: gemma2:2b | OpenAI: gpt-4o-mini | Anthropic: claude-3-5-haiku-20241022 | HF: Qwen/Qwen2.5-72B-Instruct",
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
            help="OpenAI/Anthropic/HuggingFace API 키 (Ollama는 불필요)",
            disabled=orch_provider == "ollama",
        )

        # 연결 테스트 버튼
        if st.button("연결 테스트", key="test_orch"):
            _test_llm_connection(orch_provider, orch_base_url, orch_api_key, "오케스트레이터")

    with col_agent:
        st.markdown("### 에이전트 설정")
        st.caption("SQL 생성, RAG 등 데이터 처리에 사용됩니다. 민감 데이터 보호를 위해 로컬 모델을 권장합니다.")

        _agent_providers = ["ollama", "openai", "anthropic", "huggingface"]
        _agent_current = current_settings.get("agent_provider", "ollama")
        if _agent_current not in _agent_providers:
            _agent_current = "ollama"
        agent_provider = st.selectbox(
            "프로바이더",
            options=_agent_providers,
            index=_agent_providers.index(_agent_current),
            key="agent_provider",
            disabled=airgapped,
        )

        agent_model = st.text_input(
            "모델명",
            value=current_settings.get("agent_model", ""),
            key="agent_model",
            help="Ollama: gemma2:2b | OpenAI: gpt-4o | Anthropic: claude-3-5-sonnet-20241022 | HF: meta-llama/Llama-3.1-8B-Instruct",
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
            help="OpenAI/Anthropic/HuggingFace API 키 (Ollama는 불필요)",
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
| **제한적 인터넷** | OpenAI/Anthropic/HuggingFace | Ollama (권장) |
| **일반 환경** | OpenAI/Anthropic/HuggingFace | Ollama 또는 상용/HF |
| **GPU 없는 로컬** | HuggingFace (권장) | HuggingFace (권장) |

**프로바이더별 모델 예시:**

- **Ollama**: `gemma2:2b`, `llama3.1:8b`, `qwen2.5:7b`
- **OpenAI**: `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`
- **Anthropic**: `claude-3-5-sonnet-20241022`, `claude-3-5-haiku-20241022`
- **HuggingFace**: `Qwen/Qwen2.5-72B-Instruct`, `meta-llama/Llama-3.1-8B-Instruct`, `mistralai/Mistral-7B-Instruct-v0.3`

**HuggingFace Inference API:**
- 무료 티어 사용 가능 (rate limit 있음)
- API 키: https://huggingface.co/settings/tokens 에서 발급
- 서버사이드 GPU 추론으로 로컬 GPU 없이도 빠른 응답 가능
- base_url은 비워두면 됩니다 (기본 HF 엔드포인트 사용)

**보안 고려사항:**
- 에이전트는 스키마 정보와 쿼리 결과를 LLM에 전달합니다
- 민감 데이터 보호를 위해 에이전트는 로컬 모델(Ollama) 사용을 권장합니다
- 오케스트레이터는 질의 텍스트만 전달하므로 상용 모델 사용 시 위험이 낮습니다
- HuggingFace도 외부 API이므로 민감 데이터 전송 시 주의가 필요합니다
        """)


def _render_ollama_compute_status():
    """Ollama GPU/CPU 실행 상태를 표시."""
    try:
        from agent._llm import check_ollama_compute_status
        from config.settings import get_settings

        settings = get_settings()
        status = check_ollama_compute_status(settings.ollama.host)

        if not status["connected"]:
            st.warning(f"Ollama 연결 불가: {status['message']}")
            return

        device = status["compute_device"]
        rec_timeout = status["recommended_timeout"]

        # 실행 환경 표시
        with st.container(border=True):
            col_device, col_timeout, col_models = st.columns(3)

            with col_device:
                if device == "gpu":
                    st.markdown("**실행 환경**")
                    st.success(f"🚀 GPU 가속")
                    if status.get("vram_used_mb"):
                        st.caption(f"VRAM 사용: {status['vram_used_mb']}MB")
                else:
                    st.markdown("**실행 환경**")
                    st.warning(f"🐢 CPU 모드")
                    st.caption("GPU 미사용 — 응답 느림")

            with col_timeout:
                st.markdown("**타임아웃**")
                current_timeout = settings.ollama.timeout
                if device == "cpu" and current_timeout < rec_timeout:
                    st.info(f"⏱ {rec_timeout}초 (자동 상향)")
                    st.caption(f"env 설정: {current_timeout}초")
                else:
                    st.info(f"⏱ {current_timeout}초")

            with col_models:
                st.markdown("**로드된 모델**")
                if status["loaded_models"]:
                    for m in status["loaded_models"]:
                        vram_info = ""
                        if m["size_vram_mb"] > 0:
                            vram_info = f" (VRAM {m['size_vram_mb']}MB)"
                        else:
                            vram_info = " (CPU)"
                        st.caption(f"• {m['name']}{vram_info}")
                else:
                    st.caption("없음 (첫 요청 시 로드)")

        # 설치된 모델 목록 (접을 수 있는 영역)
        if status["installed_models"]:
            with st.expander(f"설치된 모델 ({len(status['installed_models'])}개)"):
                for m in status["installed_models"]:
                    size_gb = m["size_mb"] / 1024
                    st.caption(
                        f"• **{m['name']}** — {size_gb:.1f}GB "
                        f"({m['parameter_size']}, {m['family']})"
                    )

    except Exception as e:
        st.warning(f"Ollama 환경 확인 실패: {e}")


def _render_network_status():
    """네트워크 연결 상태를 표시."""
    st.markdown("### 네트워크 상태")

    try:
        from agent._llm import check_network_connectivity
        status = check_network_connectivity()

        col1, col2, col3, col4, col5 = st.columns(5)

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
            if status.get("huggingface_reachable"):
                st.success("HuggingFace: 접근 가능")
            else:
                st.warning("HuggingFace: 접근 불가")

        with col5:
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
