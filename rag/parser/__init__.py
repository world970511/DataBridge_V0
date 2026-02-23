"""
문서 파서 패키지.

각 포맷별 파서 모듈과 공통 예외 클래스를 제공합니다.
"""


class EncryptedFileError(Exception):
    """암호화된 파일을 파싱할 수 없을 때 발생하는 예외."""

    def __init__(self, file_path: str, file_type: str, message: str = None):
        self.file_path = file_path
        self.file_type = file_type
        self.message = message or f"Encrypted {file_type} file: {file_path}"
        super().__init__(self.message)
