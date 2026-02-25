"""
알림 이벤트 타입 상수 정의.

모든 이벤트 타입은 '{카테고리}.{액션}' 형식을 따릅니다.
구독 시 와일드카드 패턴을 사용할 수 있습니다:
    - 'file.*'  → 파일 관련 모든 이벤트
    - 'job.*'   → 배치 잡 관련 모든 이벤트
    - '*'       → 전체 이벤트
"""

# ── 파일 처리 ──
FILE_REGISTERED = "file.registered"   # 파일 분류 완료
FILE_LOADED = "file.loaded"           # DB/ChromaDB 적재 완료
FILE_FAILED = "file.failed"           # 처리 실패
FILE_DELETED = "file.deleted"         # 파일 삭제 (DB/임베딩 정리 포함)

# ── 데이터 마트 ──
MART_CREATED = "mart.created"         # 마트 테이블 생성 완료
MART_FAILED = "mart.failed"           # 마트 생성 실패

# ── 배치 잡 ──
JOB_COMPLETED = "job.completed"       # 배치 잡 실행 성공
JOB_FAILED = "job.failed"             # 배치 잡 실행 실패

# ── 외부 DB ──
EXTERNAL_DB_SYNCED = "external_db.synced"  # 외부 DB 스키마 동기화 완료

# ── 승인 ──
APPROVAL_REQUESTED = "approval.requested"  # 승인 요청 생성
APPROVAL_RESOLVED = "approval.resolved"    # 승인/거부 완료

# 이벤트 카테고리 목록 (UI selectbox용)
EVENT_PATTERNS = [
    "*",
    "file.*",
    FILE_REGISTERED,
    FILE_LOADED,
    FILE_FAILED,
    FILE_DELETED,
    "mart.*",
    MART_CREATED,
    MART_FAILED,
    "job.*",
    JOB_COMPLETED,
    JOB_FAILED,
    "external_db.*",
    EXTERNAL_DB_SYNCED,
    "approval.*",
    APPROVAL_REQUESTED,
    APPROVAL_RESOLVED,
]

# 채널 목록
CHANNELS = ["webhook", "slack", "teams"]
