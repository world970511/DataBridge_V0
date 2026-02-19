"""
LLM 설정 관리 모듈.

데이터베이스의 llm_settings 테이블에서 런타임 LLM 설정을 로드/저장합니다.
환경 변수 설정보다 DB 설정이 우선 적용되어, 서비스 재시작 없이 모델 변경이 가능합니다.

주요 함수:
    get_llm_settings() -> dict
        - DB에서 현재 LLM 설정을 조회하여 딕셔너리로 반환
    save_llm_setting(key, value, updated_by) -> bool
        - 개별 설정값을 DB에 저장
    apply_db_settings_to_config() -> Settings
        - DB 설정을 Settings 싱글톤에 적용 (서비스 재시작 효과)

의존 모듈:
    - db.connection: execute_query(), execute_write()
    - config.settings: get_settings(), LLMProviderConfig, LLMConfig
"""

import logging
from typing import Optional

from db.connection import execute_query

logger = logging.getLogger(__name__)

# DB에서 관리하는 설정 키 목록
LLM_SETTING_KEYS = [
    "orchestrator_provider",
    "orchestrator_model",
    "orchestrator_api_key",
    "orchestrator_base_url",
    "agent_provider",
    "agent_model",
    "agent_api_key",
    "agent_base_url",
    "airgapped_mode",
]


def get_llm_settings() -> dict:
    """
    DB에서 현재 LLM 설정을 조회하여 딕셔너리로 반환.

    Returns:
        {
            "orchestrator_provider": "ollama",
            "orchestrator_model": "exaone3.5:7.8b",
            "orchestrator_api_key": "",
            "orchestrator_base_url": "http://localhost:11434",
            "agent_provider": "ollama",
            "agent_model": "exaone3.5:7.8b",
            "agent_api_key": "",
            "agent_base_url": "http://localhost:11434",
            "airgapped_mode": "false",
        }

        DB 조회 실패 시 빈 딕셔너리 반환.
    """
    try:
        rows = execute_query(
            "SELECT setting_key, setting_value FROM llm_settings"
        )
        if rows:
            return {row["setting_key"]: row["setting_value"] or "" for row in rows}
        return {}
    except Exception as e:
        logger.error(f"Failed to load LLM settings from DB: {e}")
        return {}


def save_llm_setting(
    key: str,
    value: str,
    updated_by: str = "admin",
    is_encrypted: bool = False,
) -> bool:
    """
    개별 LLM 설정값을 DB에 저장.

    Args:
        key: 설정 키 (예: "orchestrator_provider")
        value: 설정 값
        updated_by: 변경한 사용자 ID
        is_encrypted: API 키 등 민감 정보 여부

    Returns:
        저장 성공 여부.
    """
    if key not in LLM_SETTING_KEYS:
        logger.warning(f"Invalid LLM setting key: {key}")
        return False

    try:
        from db.connection import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE llm_settings
                    SET setting_value = %s,
                        is_encrypted = %s,
                        updated_by = %s,
                        updated_at = NOW()
                    WHERE setting_key = %s
                    """,
                    (value, is_encrypted, updated_by, key),
                )
                if cur.rowcount == 0:
                    # 키가 없으면 INSERT
                    cur.execute(
                        """
                        INSERT INTO llm_settings (setting_key, setting_value, is_encrypted, updated_by)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (key, value, is_encrypted, updated_by),
                    )
            conn.commit()
        logger.info(f"LLM setting saved: {key}={'***' if is_encrypted else value}")
        return True
    except Exception as e:
        logger.error(f"Failed to save LLM setting {key}: {e}")
        return False


def save_all_llm_settings(settings: dict, updated_by: str = "admin") -> bool:
    """
    여러 LLM 설정값을 한 번에 저장.

    Args:
        settings: {setting_key: setting_value} 딕셔너리
        updated_by: 변경한 사용자 ID

    Returns:
        모든 설정 저장 성공 여부.
    """
    success = True
    for key, value in settings.items():
        is_encrypted = "api_key" in key
        if not save_llm_setting(key, value, updated_by, is_encrypted):
            success = False
    return success


def apply_db_settings_to_config():
    """
    DB에 저장된 LLM 설정을 Settings 싱글톤에 적용.

    이 함수를 호출하면 DB 설정이 환경 변수 설정을 덮어씁니다.
    서비스 재시작 없이 모델 변경 효과를 얻을 수 있습니다.

    Returns:
        업데이트된 Settings 객체.
    """
    from config.settings import get_settings, LLMProviderConfig, LLMConfig

    db_settings = get_llm_settings()
    if not db_settings:
        logger.debug("No DB LLM settings found, using environment defaults")
        return get_settings()

    settings = get_settings()

    # 오케스트레이터 설정 적용
    if db_settings.get("orchestrator_provider"):
        settings.llm.orchestrator = LLMProviderConfig(
            provider=db_settings.get("orchestrator_provider", "ollama"),
            model=db_settings.get("orchestrator_model", settings.ollama.model),
            api_key=db_settings.get("orchestrator_api_key", ""),
            base_url=db_settings.get("orchestrator_base_url", settings.ollama.host),
        )

    # 에이전트 설정 적용
    if db_settings.get("agent_provider"):
        settings.llm.agent = LLMProviderConfig(
            provider=db_settings.get("agent_provider", "ollama"),
            model=db_settings.get("agent_model", settings.ollama.model),
            api_key=db_settings.get("agent_api_key", ""),
            base_url=db_settings.get("agent_base_url", settings.ollama.host),
        )

    # 폐쇄망 모드 적용
    settings.llm.airgapped_mode = db_settings.get("airgapped_mode", "false").lower() == "true"

    logger.info(
        f"Applied DB LLM settings: "
        f"orchestrator={settings.llm.orchestrator.provider}/{settings.llm.orchestrator.model}, "
        f"agent={settings.llm.agent.provider}/{settings.llm.agent.model}, "
        f"airgapped={settings.llm.airgapped_mode}"
    )

    return settings


def get_available_models(provider: str, base_url: str = "", api_key: str = "") -> list[str]:
    """
    지정된 프로바이더에서 사용 가능한 모델 목록을 조회.

    Args:
        provider: 프로바이더 ("ollama", "openai", "anthropic")
        base_url: Ollama의 경우 서버 URL
        api_key: OpenAI/Anthropic의 경우 API 키

    Returns:
        모델명 리스트. 조회 실패 시 빈 리스트.
    """
    from config.settings import LLMProviderConfig
    from agent._llm import check_provider_connection

    config = LLMProviderConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
    )

    result = check_provider_connection(provider, config)
    return result.get("models") or []
