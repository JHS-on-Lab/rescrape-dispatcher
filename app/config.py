"""
환경변수에서 설정을 읽는다.

값은 .env 파일 또는 실제 환경변수 어느 쪽에서든 넣을 수 있다.
서버에서는 보통 환경변수로, 로컬 개발에서는 .env 파일로 설정한다.
.env 파일이 없어도 오류가 나지 않는다.

필수 변수(RDS_*, SOLR_URL)가 없으면 워커 시작 시 validate() 가 오류를 출력하고 종료한다.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# .env (공통) 먼저 로드 후 .env.{APP_ENV} 로 override.
_root = Path(__file__).parent.parent
load_dotenv(_root / ".env")
_app_env = os.getenv("APP_ENV", "local")
load_dotenv(_root / f".env.{_app_env}", override=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


# SSH Tunnel
TUNNEL_ENABLED      = _env_bool("TUNNEL_ENABLED")
TUNNEL_SSH_HOST     = _env("TUNNEL_SSH_HOST")
TUNNEL_SSH_PORT     = _env_int("TUNNEL_SSH_PORT", 22)
TUNNEL_SSH_USER     = _env("TUNNEL_SSH_USER", "ubuntu")
TUNNEL_SSH_KEY_PATH = _env("TUNNEL_SSH_KEY_PATH")
TUNNEL_LOCAL_PORT   = _env_int("TUNNEL_LOCAL_PORT", 13307)

# RDS — keyword-crawler 와 같은 DB에 접속
RDS_HOST     = _env("RDS_HOST")
RDS_PORT     = _env_int("RDS_PORT", 3306)
RDS_USER     = _env("RDS_USER")
RDS_PASSWORD = _env("RDS_PASSWORD")
RDS_DB       = _env("RDS_DB")

# Worker
WORKER_ID = _env("WORKER_ID", "rescrape-1")

# Solr — 재수집 대상을 조회할 Solr 코어
# solr_url 은 .env 에 직접 두지 않고 t_crawl_runtime 테이블에서 조회한다.
SOLR_RUNTIME_NAME   = _env("SOLR_RUNTIME_NAME", "")  # t_crawl_runtime.runtime_name
HTTP_VERIFY_SSL     = _env_bool("HTTP_VERIFY_SSL", True)

# Solr 조회 조건
SOLR_RESCRAPE_QUERY        = _env("SOLR_RESCRAPE_QUERY", "*:*")
SOLR_RESCRAPE_URL_CONTAINS = _env("SOLR_RESCRAPE_URL_CONTAINS", "")  # 쉼표 구분 패턴. 예: naver.com,daum.net
SOLR_RESCRAPE_RUNTIME_KEY  = _env("SOLR_RESCRAPE_RUNTIME_KEY", "")   # crawl_runtime_key 필터값. 비어있으면 필터 미적용
SLIDING_WINDOW_MINUTES     = _env_int("SLIDING_WINDOW_MINUTES", 10)   # 슬라이딩 윈도우 크기(분). 주기의 2배 권장
SOLR_QUERY_BATCH_SIZE      = _env_int("SOLR_QUERY_BATCH_SIZE", 100)
SOLR_RESCRAPE_MAX_DOCS     = _env_int("SOLR_RESCRAPE_MAX_DOCS", 1000)

# 재수집 source_type 고정값 — Solr 에서 오는 URL 임을 식별하기 위한 상수
SOLR_RESCRAPE_SOURCE_TYPE  = "SOLR_RESCRAPE"

# Dispatch
DISPATCH_INTERVAL_SECONDS = _env_int("DISPATCH_INTERVAL_SECONDS", 300)
RESCRAPE_PRIORITY         = _env_int("RESCRAPE_PRIORITY", 5)

# Logging
LOG_DIR                    = _env("LOG_DIR", "./logs")
LOG_LEVEL                  = _env("LOG_LEVEL", "INFO")
LOG_ROTATION               = _env("LOG_ROTATION", "daily")
HEARTBEAT_INTERVAL_SECONDS = _env_int("HEARTBEAT_INTERVAL_SECONDS", 60)


# ---------------------------------------------------------------------------
# 시작 시 검증
# ---------------------------------------------------------------------------

_REQUIRED_ALWAYS = ["RDS_HOST", "RDS_USER", "RDS_PASSWORD", "RDS_DB", "SOLR_RUNTIME_NAME"]
_REQUIRED_TUNNEL = ["TUNNEL_SSH_HOST", "TUNNEL_SSH_KEY_PATH"]


def validate() -> None:
    """
    필수 환경변수를 일괄 검증한다.
    누락 항목이 있으면 목록을 stderr 에 출력하고 sys.exit(1).
    __main__.py 에서 진입 전에 호출한다.
    """
    missing = [k for k in _REQUIRED_ALWAYS if not os.getenv(k)]

    if TUNNEL_ENABLED:
        missing += [k for k in _REQUIRED_TUNNEL if not os.getenv(k)]

    if not missing:
        return

    print("ERROR: 다음 필수 환경변수가 설정되지 않았습니다:", file=sys.stderr)
    for key in missing:
        print(f"  - {key}", file=sys.stderr)
    print("  .env 파일 또는 환경변수를 확인하세요.", file=sys.stderr)
    sys.exit(1)
