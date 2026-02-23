"""
에이전트 도구 패키지.

SQL 에이전트, 문서 에이전트, 이미지 에이전트가 사용하는 개별 도구를 모아놓은
패키지입니다. 각 도구는 기존 인프라 모듈(db.connection, catalog.catalog,
rag.embedder)을 래핑하여 에이전트에 최적화된 인터페이스를 제공합니다.

도구 목록:
    - query_db: SQL SELECT/WRITE 검증 및 실행
    - list_tables: 카탈로그 테이블 목록 및 스키마 정보
    - search_docs: 문서 검색 (ChromaDB RAG)
    - search_images: 이미지 검색 (DINOv2 임베딩)
    - create_mart: 데이터 마트 빌더 (CREATE TABLE AS SELECT)
    - manage_jobs: 배치 작업 관리 (CRUD + 수동 실행)
"""
