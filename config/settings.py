"""
DataBridge 설정 로딩 모듈.
환경 변수(.env)에서 설정을 읽어 전역에서 사용.
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
    """환경 변수에서 설정을 로드하여 Settings 객체를 반환."""
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
    """전역 Settings 싱글톤 반환."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
