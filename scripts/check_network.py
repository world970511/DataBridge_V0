#!/usr/bin/env python3
"""
DataBridge 네트워크 연결 검증 스크립트.

Docker 컨테이너와 호스트 Ollama 간 연결 상태를 점검하고,
문제 발생 시 해결 방법을 안내합니다.

사용법:
    python scripts/check_network.py
"""

import os
import platform
import socket
import subprocess
import sys

# requests가 없을 경우를 대비한 폴백
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def print_header(title: str) -> None:
    """섹션 헤더 출력."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(name: str, success: bool, detail: str = "") -> None:
    """검사 결과 출력."""
    status = "[OK]" if success else "[FAIL]"
    print(f"  {status} {name}")
    if detail:
        print(f"       -> {detail}")


def detect_environment() -> dict:
    """
    실행 환경 감지.

    Returns:
        환경 정보 딕셔너리
    """
    info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "python": platform.python_version(),
        "in_docker": os.path.exists("/.dockerenv"),
        "in_wsl": False,
        "docker_desktop": False,
    }

    # WSL 감지
    if info["os"] == "Linux":
        try:
            with open("/proc/version", "r") as f:
                version = f.read().lower()
                if "microsoft" in version or "wsl" in version:
                    info["in_wsl"] = True
        except Exception:
            pass

    # Docker Desktop 감지 (Windows/Mac)
    if info["os"] in ("Windows", "Darwin"):
        info["docker_desktop"] = True

    return info


def check_ollama_local(host: str = "localhost", port: int = 11434) -> tuple[bool, str]:
    """
    로컬 Ollama 서버 연결 확인.

    Args:
        host: Ollama 호스트
        port: Ollama 포트

    Returns:
        (성공 여부, 상세 메시지)
    """
    url = f"http://{host}:{port}/api/tags"

    if not HAS_REQUESTS:
        # requests 없이 소켓으로 확인
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                return True, f"포트 {port} 열림 (HTTP 확인 불가 - requests 미설치)"
            else:
                return False, f"포트 {port} 연결 실패"
        except Exception as e:
            return False, str(e)

    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            if models:
                return True, f"설치된 모델: {', '.join(models[:3])}"
            else:
                return True, "연결 성공 (설치된 모델 없음)"
        else:
            return False, f"HTTP {resp.status_code}"
    except requests.ConnectionError:
        return False, "연결 거부됨 - Ollama가 실행 중인지 확인하세요"
    except requests.Timeout:
        return False, "타임아웃"
    except Exception as e:
        return False, str(e)


def check_host_docker_internal() -> tuple[bool, str]:
    """
    host.docker.internal DNS 해석 확인.

    Returns:
        (성공 여부, 상세 메시지)
    """
    try:
        ip = socket.gethostbyname("host.docker.internal")
        return True, f"해석됨: {ip}"
    except socket.gaierror:
        return False, "DNS 해석 실패"
    except Exception as e:
        return False, str(e)


def check_docker_running() -> tuple[bool, str]:
    """
    Docker 데몬 실행 확인.

    Returns:
        (성공 여부, 상세 메시지)
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return True, "Docker 데몬 실행 중"
        else:
            return False, "Docker 데몬 응답 없음"
    except FileNotFoundError:
        return False, "docker 명령어를 찾을 수 없음"
    except subprocess.TimeoutExpired:
        return False, "타임아웃"
    except Exception as e:
        return False, str(e)


def check_container_to_host(port: int = 11434) -> tuple[bool, str]:
    """
    Docker 컨테이너에서 호스트 Ollama 연결 테스트.

    Args:
        port: Ollama 포트

    Returns:
        (성공 여부, 상세 메시지)
    """
    try:
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "alpine",
                "sh", "-c",
                f"apk add --no-cache curl > /dev/null 2>&1 && "
                f"curl -s -o /dev/null -w '%{{http_code}}' "
                f"http://host.docker.internal:{port}/api/tags"
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        output = result.stdout.strip()
        if output == "200":
            return True, "컨테이너에서 호스트 Ollama 연결 성공"
        elif output:
            return False, f"HTTP 응답: {output}"
        else:
            return False, "연결 실패 또는 타임아웃"
    except subprocess.TimeoutExpired:
        return False, "타임아웃 (30초)"
    except Exception as e:
        return False, str(e)


def print_recommendations(env: dict, results: dict) -> None:
    """
    검사 결과에 따른 권장 사항 출력.

    Args:
        env: 환경 정보
        results: 검사 결과
    """
    print_header("권장 사항")

    # Ollama 로컬 연결 실패
    if not results.get("ollama_local", (False,))[0]:
        print("""
  [Ollama 실행 안 됨]

  1. Ollama 설치 확인:
     - https://ollama.com/download

  2. Ollama 서비스 시작:
     - Linux: sudo systemctl start ollama
     - Windows/Mac: ollama serve

  3. 모델 다운로드:
     - ollama pull exaone3.5:7.8b
""")

    # host.docker.internal 해석 실패
    if not results.get("host_docker_internal", (False,))[0]:
        if env["os"] == "Linux" and not env["in_wsl"]:
            print("""
  [Linux Docker: host.docker.internal 미지원]

  docker-compose.yml에 아래 설정 추가:

  services:
    app:
      extra_hosts:
        - "host.docker.internal:host-gateway"
""")
        else:
            print("""
  [host.docker.internal 해석 실패]

  Docker Desktop을 재시작하거나 업데이트하세요.
""")

    # 컨테이너에서 호스트 연결 실패
    if not results.get("container_to_host", (False,))[0]:
        print("""
  [컨테이너에서 호스트 Ollama 연결 실패]

  1. Ollama가 모든 인터페이스에서 수신하도록 설정:
     - Linux: Environment="OLLAMA_HOST=0.0.0.0" (systemd)
     - Windows: set OLLAMA_HOST=0.0.0.0 (환경 변수)

  2. 방화벽에서 11434 포트 허용

  3. .env 파일 확인:
     OLLAMA_HOST=http://host.docker.internal:11434
""")


def main() -> int:
    """
    메인 함수.

    Returns:
        종료 코드 (0: 성공, 1: 일부 실패)
    """
    print("\n" + "="*60)
    print("  DataBridge 네트워크 연결 검증")
    print("="*60)

    # 환경 감지
    print_header("환경 정보")
    env = detect_environment()
    print(f"  OS: {env['os']} {env['os_release']}")
    print(f"  Python: {env['python']}")
    print(f"  Docker 컨테이너 내부: {'Yes' if env['in_docker'] else 'No'}")
    print(f"  WSL: {'Yes' if env['in_wsl'] else 'No'}")
    print(f"  Docker Desktop: {'Yes (예상)' if env['docker_desktop'] else 'No'}")

    results = {}
    all_passed = True

    # 1. Ollama 로컬 연결 확인
    print_header("1. 로컬 Ollama 연결")
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    # URL에서 호스트와 포트 추출
    if "://" in ollama_host:
        host_port = ollama_host.split("://")[1]
    else:
        host_port = ollama_host

    if ":" in host_port:
        host, port = host_port.split(":")
        port = int(port)
    else:
        host = host_port
        port = 11434

    # localhost로 테스트
    success, detail = check_ollama_local("localhost", port)
    results["ollama_local"] = (success, detail)
    print_result(f"localhost:{port}", success, detail)
    if not success:
        all_passed = False

    # 2. Docker 데몬 확인
    print_header("2. Docker 데몬")
    success, detail = check_docker_running()
    results["docker"] = (success, detail)
    print_result("Docker 데몬", success, detail)
    if not success:
        all_passed = False

    # Docker가 실행 중일 때만 추가 테스트
    if results["docker"][0]:
        # 3. host.docker.internal 확인
        print_header("3. host.docker.internal DNS")
        success, detail = check_host_docker_internal()
        results["host_docker_internal"] = (success, detail)
        print_result("host.docker.internal", success, detail)
        if not success:
            all_passed = False

        # 4. 컨테이너에서 호스트 Ollama 연결
        print_header("4. 컨테이너 → 호스트 Ollama")
        print("  (테스트 중... 최대 30초 소요)")
        success, detail = check_container_to_host(port)
        results["container_to_host"] = (success, detail)
        print_result("컨테이너에서 Ollama 연결", success, detail)
        if not success:
            all_passed = False

    # 권장 사항
    if not all_passed:
        print_recommendations(env, results)

    # 결과 요약
    print_header("결과 요약")
    if all_passed:
        print("  모든 검사 통과! DataBridge를 실행할 준비가 되었습니다.")
        print("\n  실행: docker compose up -d")
        return 0
    else:
        print("  일부 검사 실패. 위의 권장 사항을 확인하세요.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
