"""
DataBridge 설정 로딩 모듈.

.env 파일 또는 시스템 환경 변수에서 설정값을 읽어와
DatabaseConfig, ChromaConfig, OllamaConfig, AgentConfig, WatcherConfig
다섯 개의 데이터클래스로 구성된 Settings 객체를 생성합니다.
load_settings()로 매번 새로 로드하거나, get_settings()로 싱글톤 인스턴스를
사용하여 애플리케이션 전역에서 동일한 설정을 공유합니다.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DatabaseConfig:
    url: str = ""
    host: str = "postgres"
    port: int = 5432
    name: str = "adf"
    user: str = "adf"
    password: str = "changeme"

    def __post_init__(self):
        if not self.url:
            self.url = f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


@dataclass
class ChromaConfig:
    host: str = "chromadb"
    port: int = 8000

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class OllamaConfig:
    host: str = "http://ollama:11434"
    model: str = "exaone3.5:7.8b"


@dataclass
class AgentConfig:
    max_query_rows: int = 5000
    query_timeout: int = 30
    mart_prefix: str = "mart_"


@dataclass
class WatcherConfig:
    watch_dir: str = "/data"
    webhook_enabled: bool = True
    webhook_secret: str = "changeme"


@dataclass
class Settings:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    app_port: int = 8501
    job_log_dir: str = "./logs/jobs"


def load_settings() -> Settings:
    """
    환경 변수에서 각 서비스별 설정을 읽어 Settings 객체를 생성하여 반환.

    os.getenv()를 통해 DATABASE_URL, POSTGRES_HOST, CHROMA_HOST, OLLAMA_HOST 등
    환경 변수를 조회하며, 값이 없으면 개발 환경용 기본값(예: postgres, chromadb)을 사용합니다.
    Returns: 모든 서비스 설정이 포함된 Settings 데이터클래스 인스턴스.
    """
    db = DatabaseConfig(
        url=os.getenv("DATABASE_URL", ""),
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        name=os.getenv("POSTGRES_DB", "adf"),
        user=os.getenv("POSTGRES_USER", "adf"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme"),
    )

    chroma = ChromaConfig(
        host=os.getenv("CHROMA_HOST", "chromadb"),
        port=int(os.getenv("CHROMA_PORT", "8000")),
    )

    ollama = OllamaConfig(
        host=os.getenv("OLLAMA_HOST", "http://ollama:11434"),
        model=os.getenv("LLM_MODEL", "exaone3.5:7.8b"),
    )

    agent = AgentConfig(
        max_query_rows=int(os.getenv("AGENT_MAX_QUERY_ROWS", "5000")),
        query_timeout=int(os.getenv("AGENT_QUERY_TIMEOUT", "30")),
        mart_prefix=os.getenv("MART_PREFIX", "mart_"),
    )

    watcher = WatcherConfig(
        watch_dir=os.getenv("WATCH_DIR", "/data"),
        webhook_enabled=os.getenv("WEBHOOK_ENABLED", "true").lower() == "true",
        webhook_secret=os.getenv("WEBHOOK_SECRET", "changeme"),
    )

    return Settings(
        db=db,
        chroma=chroma,
        ollama=ollama,
        agent=agent,
        watcher=watcher,
        app_port=int(os.getenv("APP_PORT", "8501")),
        job_log_dir=os.getenv("JOB_LOG_DIR", "./logs/jobs"),
    )


# 싱글톤 인스턴스
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """
    전역 Settings 싱글톤 인스턴스를 반환.

    최초 호출 시 load_settings()를 통해 환경 변수에서 설정을 로드하고,
    이후 호출에서는 이미 생성된 동일 인스턴스를 재사용합니다.
    Returns: 애플리케이션 전역에서 공유되는 Settings 인스턴스.
    """
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
