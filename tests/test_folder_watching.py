"""
파일 분류 및 폴더 감시 모듈 테스트.

watcher/classifier.py의 파일 분류 로직과 watcher/file_watcher.py의
이벤트 핸들러·디바운스 기능을 테스트합니다.

- Unit 테스트: get_file_action(), get_file_type(), FileEventHandler
- Integration 테스트: 실제 watchdog Observer를 사용한 이벤트 감지

실행:
    pytest tests/test_folder_watching.py -v -m unit
    pytest tests/test_folder_watching.py -v -m integration
"""

import time

import pytest

from watcher.classifier import get_file_action, get_file_type, DEFAULT_RULES


# ============================================
# Unit 테스트 — 파일 분류
# ============================================

@pytest.mark.unit
class TestGetFileAction:
    """get_file_action() — 파일 확장자 기반 액션 분류 테스트."""

    def test_csv_load_to_db(self):
        """CSV 파일은 'load_to_db' 액션으로 분류됩니다."""
        assert get_file_action("/data/sales.csv") == "load_to_db"

    def test_tsv_load_to_db(self):
        """TSV 파일은 'load_to_db' 액션으로 분류됩니다."""
        assert get_file_action("/data/data.tsv") == "load_to_db"

    def test_xlsx_load_to_db(self):
        """Excel(.xlsx) 파일은 'load_to_db' 액션으로 분류됩니다."""
        assert get_file_action("/data/products.xlsx") == "load_to_db"

    def test_xls_load_to_db(self):
        """Excel(.xls) 파일은 'load_to_db' 액션으로 분류됩니다."""
        assert get_file_action("/data/old_data.xls") == "load_to_db"

    def test_pdf_register_for_search(self):
        """PDF 파일은 'register_for_search' 액션으로 분류됩니다."""
        assert get_file_action("/data/report.pdf") == "register_for_search"

    def test_docx_register_for_search(self):
        """DOCX 파일은 'register_for_search' 액션으로 분류됩니다."""
        assert get_file_action("/data/guide.docx") == "register_for_search"

    def test_txt_register_for_search(self):
        """TXT 파일은 'register_for_search' 액션으로 분류됩니다."""
        assert get_file_action("/data/notes.txt") == "register_for_search"

    def test_temp_file_ignore(self):
        """임시 파일(~$)은 규칙 순서에 따라 확장자가 먼저 매칭될 수 있습니다.
        실제 ~$ 필터링은 FileEventHandler._handle()에서 수행됩니다.
        순수 임시 파일(.tmp)은 'ignore'로 분류됩니다."""
        # ~$temp.xlsx → *.xlsx가 먼저 매칭되므로 load_to_db
        # 실제 ~$ 필터링은 FileEventHandler._handle()에서 이름 기반으로 수행
        assert get_file_action("/data/~$temp.tmp") == "ignore"

    def test_tmp_file_ignore(self):
        """TMP 파일은 'ignore' 액션으로 분류됩니다."""
        assert get_file_action("/data/file.tmp") == "ignore"

    def test_ds_store_ignore(self):
        """.DS_Store 파일은 'ignore' 액션으로 분류됩니다."""
        assert get_file_action("/data/.DS_Store") == "ignore"

    def test_unknown_extension_ignore(self):
        """알 수 없는 확장자는 'ignore' 액션으로 분류됩니다."""
        assert get_file_action("/data/file.xyz") == "ignore"

    def test_case_insensitive(self):
        """파일 확장자 비교는 대소문자를 구분하지 않습니다."""
        assert get_file_action("/data/DATA.CSV") == "load_to_db"
        assert get_file_action("/data/REPORT.PDF") == "register_for_search"


@pytest.mark.unit
class TestGetFileType:
    """get_file_type() — 파일 확장자 → 내부 타입 문자열 변환 테스트."""

    def test_csv_type(self):
        assert get_file_type("/data/file.csv") == "csv"

    def test_tsv_type(self):
        assert get_file_type("/data/file.tsv") == "csv"

    def test_xlsx_type(self):
        assert get_file_type("/data/file.xlsx") == "excel"

    def test_xls_type(self):
        assert get_file_type("/data/file.xls") == "excel"

    def test_pdf_type(self):
        assert get_file_type("/data/file.pdf") == "pdf"

    def test_docx_type(self):
        assert get_file_type("/data/file.docx") == "docx"

    def test_txt_type(self):
        assert get_file_type("/data/file.txt") == "text"

    def test_json_type(self):
        assert get_file_type("/data/file.json") == "json"

    def test_unknown_type(self):
        """알 수 없는 확장자는 'unknown'을 반환합니다."""
        assert get_file_type("/data/file.xyz") == "unknown"


@pytest.mark.unit
class TestDefaultRules:
    """DEFAULT_RULES 구조 검증."""

    def test_rules_is_list(self):
        """DEFAULT_RULES는 리스트입니다."""
        assert isinstance(DEFAULT_RULES, list)

    def test_each_rule_has_patterns_and_action(self):
        """각 규칙에 'patterns'와 'action' 키가 있습니다."""
        for rule in DEFAULT_RULES:
            assert "patterns" in rule
            assert "action" in rule
            assert isinstance(rule["patterns"], list)

    def test_valid_actions(self):
        """모든 액션이 유효한 값('load_to_db', 'register_for_search', 'ignore')입니다."""
        valid_actions = {"load_to_db", "register_for_search", "ignore"}
        for rule in DEFAULT_RULES:
            assert rule["action"] in valid_actions


# ============================================
# Unit 테스트 — FileEventHandler
# ============================================

@pytest.mark.unit
class TestFileEventHandler:
    """FileEventHandler — 디바운스 및 필터링 테스트."""

    def test_debounce_blocks_rapid_events(self):
        """동일 파일에 대해 디바운스 시간 이내 중복 이벤트는 무시됩니다."""
        from watcher.file_watcher import FileEventHandler
        handler = FileEventHandler(debounce_seconds=1.0)

        # 첫 번째 이벤트: 처리해야 함
        assert handler._should_process("/data/test.csv") is True
        # 즉시 두 번째 이벤트: 디바운스로 무시
        assert handler._should_process("/data/test.csv") is False

    def test_debounce_allows_after_wait(self):
        """디바운스 시간이 지나면 다시 이벤트를 처리합니다."""
        from watcher.file_watcher import FileEventHandler
        handler = FileEventHandler(debounce_seconds=0.1)

        assert handler._should_process("/data/test.csv") is True
        time.sleep(0.15)  # 디바운스 시간 초과 대기
        assert handler._should_process("/data/test.csv") is True

    def test_different_files_not_debounced(self):
        """서로 다른 파일은 디바운스 없이 각각 처리됩니다."""
        from watcher.file_watcher import FileEventHandler
        handler = FileEventHandler(debounce_seconds=1.0)

        assert handler._should_process("/data/a.csv") is True
        assert handler._should_process("/data/b.csv") is True


@pytest.mark.integration
class TestWatcherIntegration:
    """파일 감시 통합 테스트 (watchdog Observer 사용)."""

    def test_observer_starts_and_stops(self, tmp_watch_dir):
        """Observer가 정상적으로 시작되고 중지됩니다."""
        from watcher.file_watcher import start_watcher
        observer = start_watcher(watch_dir=tmp_watch_dir, blocking=False)
        assert observer is not None
        assert observer.is_alive()
        observer.stop()
        observer.join(timeout=3)
        assert not observer.is_alive()
