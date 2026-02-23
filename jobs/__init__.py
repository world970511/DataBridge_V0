"""
배치 작업 스케줄링 패키지.

APScheduler 기반의 배치 작업 스케줄러와 실행 엔진을 제공합니다.
batch_jobs 테이블에 등록된 활성 작업을 cron 표현식에 따라 주기적으로 실행합니다.

구성 모듈:
    - scheduler: APScheduler 기반 스케줄러 (시작/중지, DB 동기화)
    - executor: 배치 SQL 실행 엔진 (이력 기록, 에러 처리)
"""
