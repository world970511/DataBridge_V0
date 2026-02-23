"""
DataBridge 설정 로딩 모듈.

.env 파일 또는 시스템 환경 변수에서 설정값을 읽어와
DatabaseConfig, ChromaConfig, OllamaConfig, AgentConfig, WatcherConfig, AuthConfig
여섯 개의 데이터클래스로 구성된 Settings 객체를 생성합니다.
load_settings()로 매번 새로 로드하거나, get_settings()로 싱글톤 인스턴스를
사용하여 애플리케이션 전역에서 동일한 설정을 공유합니다.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 프로젝트 루트의 .env 파일 로드 (로컬 실행 시 필수, Docker에서는 무해)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=False)  # 기존 환경변수가 있으면 유지


@dataclass
class DatabaseConfig:
    url: str = ""
    host: str = "postgres"
    port: int = 5432
    name: str = "databridge"
    user: str = "admin"
    password: str = "admin1234"

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
    host: str = "http://localhost:11434"
    model: str = ""  # .env의 LLM_MODEL에서 로드 (load_settings 참조)
    timeout: int = 300  # LLM 응답 타임아웃 (초). CPU 환경에서는 자동으로 최소 600초 적용


@dataclass
class LLMProviderConfig:
    """
    개별 LLM 프로바이더 설정.

    provider: 프로바이더 유형 ("ollama", "openai", "anthropic", "huggingface")
    model: 사용할 모델명
    api_key: API 키 (상용 모델용, Ollama는 불필요. HF: hf_xxx 형식)
    base_url: API 엔드포인트 URL (Ollama용 또는 커스텀 엔드포인트, HF는 미사용)
    """
    provider: str = "ollama"
    model: str = ""  # .env의 LLM_MODEL 또는 ORCHESTRATOR/AGENT_LLM_MODEL에서 로드
    api_key: str = ""
    base_url: str = "http://localhost:11434"


@dataclass
class LLMConfig:
    """
    다중 모델 LLM 설정.

    orchestrator: 오케스트레이터용 모델 (의도 분류 등 간단한 작업)
                  - 상용 모델 사용 시 빠르고 정확한 라우팅 가능
                  - 민감 데이터 노출 위험 낮음 (질의 텍스트만 전송)

    agent: 에이전트용 모델 (SQL 생성, RAG 응답 등 데이터 처리)
           - 로컬 모델 권장 (민감 데이터 보호)
           - 스키마 정보, 쿼리 결과 등이 LLM에 전달됨

    폐쇄망 환경:
        - orchestrator와 agent 모두 ollama 사용 필수
        - 상용 API는 인터넷 연결 필요

    하이브리드 환경 (제한적 인터넷):
        - orchestrator: 상용 모델 가능 (빠른 응답, 높은 정확도)
        - agent: ollama 권장 (데이터 보안)
    """
    orchestrator: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    agent: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    # 폐쇄망 모드 여부 (True면 상용 API 비활성화)
    airgapped_mode: bool = False


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
class DocumentConfig:
    """
    문서 처리 관련 설정.

    max_embed_size_mb: 임베딩을 수행할 최대 파일 크기(MB). 이 크기를 초과하는
                       문서는 Lazy Loading 모드로 처리되어 카탈로그에만 등록되고,
                       ChromaDB 임베딩은 건너뜁니다. 질의 시 온디맨드로 파싱합니다.
    """
    max_embed_size_mb: float = 10.0


@dataclass
class ImageConfig:
    """
    이미지 처리 관련 설정.

    dino_model: DINOv2 모델 변형 ("dinov2_vits14", "dinov2_vitb14" 등).
    dino_device: torch 디바이스 ("cpu", "cuda"). 빈 문자열이면 자동 감지.
    similarity_threshold: 중복 판별 코사인 유사도 임계값 (0.0~1.0).
    near_duplicate_threshold: 유사 그룹핑 임계값.
    thumbnail_size: 썸네일 최대 크기(px).
    thumbnail_dir: 썸네일 저장 디렉토리.
    max_image_size_mb: 처리할 최대 이미지 파일 크기(MB).
    collection_name: ChromaDB 컬렉션명.
    """
    dino_model: str = "dinov2_vits14"
    dino_device: str = ""
    similarity_threshold: float = 0.98
    near_duplicate_threshold: float = 0.90
    thumbnail_size: int = 256
    thumbnail_dir: str = "/app/thumbnails"
    max_image_size_mb: float = 50.0
    collection_name: str = "images"


@dataclass
class AuthConfig:
    """
    인증 관련 설정.

    admin_password: 관리자(admin) 계정의 초기 비밀번호. 앱 기동 시
                    admin 계정이 없으면 이 비밀번호로 자동 생성됩니다.
    secret_key: 세션 서명 등에 사용할 비밀 키. 기본값은 개발 환경용이며
                프로덕션에서 반드시 변경해야 합니다.
    """
    admin_password: str = "admin1234"
    secret_key: str = "databridge-secret-key-change-me"


@dataclass
class Settings:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    document: DocumentConfig = field(default_factory=DocumentConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
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
        name=os.getenv("POSTGRES_DB", "databridge"),
        user=os.getenv("POSTGRES_USER", "admin"),
        password=os.getenv("POSTGRES_PASSWORD", "admin1234"),
    )

    chroma = ChromaConfig(
        host=os.getenv("CHROMA_HOST", "chromadb"),
        port=int(os.getenv("CHROMA_PORT", "8000")),
    )

    ollama = OllamaConfig(
        host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        model=os.getenv("LLM_MODEL", "gemma2:2b"),
        timeout=int(os.getenv("LLM_TIMEOUT", "300")),
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

    auth = AuthConfig(
        admin_password=os.getenv("ADMIN_PASSWORD", "admin1234"),
        secret_key=os.getenv("SECRET_KEY", "databridge-secret-key-change-me"),
    )

    document = DocumentConfig(
        max_embed_size_mb=float(os.getenv("MAX_EMBED_SIZE_MB", "10.0")),
    )

    image = ImageConfig(
        dino_model=os.getenv("DINO_MODEL", "dinov2_vits14"),
        dino_device=os.getenv("DINO_DEVICE", ""),
        similarity_threshold=float(os.getenv("IMAGE_SIMILARITY_THRESHOLD", "0.98")),
        near_duplicate_threshold=float(os.getenv("IMAGE_NEAR_DUPLICATE_THRESHOLD", "0.90")),
        thumbnail_size=int(os.getenv("IMAGE_THUMBNAIL_SIZE", "256")),
        thumbnail_dir=os.getenv("IMAGE_THUMBNAIL_DIR", "/app/thumbnails"),
        max_image_size_mb=float(os.getenv("IMAGE_MAX_SIZE_MB", "50.0")),
        collection_name=os.getenv("IMAGE_COLLECTION_NAME", "images"),
    )

    # LLM 다중 모델 설정
    # 오케스트레이터: 의도 분류 등 간단한 작업 (상용 모델 가능)
    orchestrator_llm = LLMProviderConfig(
        provider=os.getenv("ORCHESTRATOR_LLM_PROVIDER", "ollama"),
        model=os.getenv("ORCHESTRATOR_LLM_MODEL", ollama.model),
        api_key=os.getenv("ORCHESTRATOR_LLM_API_KEY", ""),
        base_url=os.getenv("ORCHESTRATOR_LLM_BASE_URL", ollama.host),
    )

    # 에이전트: SQL 생성, RAG 등 데이터 처리 (로컬 모델 권장)
    agent_llm = LLMProviderConfig(
        provider=os.getenv("AGENT_LLM_PROVIDER", "ollama"),
        model=os.getenv("AGENT_LLM_MODEL", ollama.model),
        api_key=os.getenv("AGENT_LLM_API_KEY", ""),
        base_url=os.getenv("AGENT_LLM_BASE_URL", ollama.host),
    )

    llm = LLMConfig(
        orchestrator=orchestrator_llm,
        agent=agent_llm,
        airgapped_mode=os.getenv("AIRGAPPED_MODE", "false").lower() == "true",
    )

    return Settings(
        db=db,
        chroma=chroma,
        ollama=ollama,
        llm=llm,
        agent=agent,
        watcher=watcher,
        document=document,
        image=image,
        auth=auth,
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
