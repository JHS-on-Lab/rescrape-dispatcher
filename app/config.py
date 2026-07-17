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
    # os.getenv(key, default)는 key가 아예 없을 때만 default를 쓰고, .env 파일에
    # "KEY=" 처럼 빈 값으로 정의된 경우(존재는 하지만 빈 문자열)는 그대로 ""를
    # 반환한다 — 폴백이 안 걸린다. 빈 문자열도 "미설정"으로 취급해야 RDS_TRENDTRACKER_*
    # 처럼 ".env에 키만 두고 값은 비워서 상위 값으로 폴백"하는 패턴이 의도대로 동작한다.
    return os.getenv(key) or default


def _env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    return int(val) if val else default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if not val:
        return default
    return val.lower() in ("true", "1", "yes")


# SSH Tunnel
TUNNEL_ENABLED      = _env_bool("TUNNEL_ENABLED")
TUNNEL_SSH_HOST     = _env("TUNNEL_SSH_HOST")
TUNNEL_SSH_PORT     = _env_int("TUNNEL_SSH_PORT", 22)
TUNNEL_SSH_USER     = _env("TUNNEL_SSH_USER", "ubuntu")
TUNNEL_SSH_KEY_PATH = _env("TUNNEL_SSH_KEY_PATH")
TUNNEL_LOCAL_PORT   = _env_int("TUNNEL_LOCAL_PORT", 13307)

# RDS — keyword-crawler 와 같은 DB 서버에 접속
RDS_HOST             = _env("RDS_HOST")
RDS_PORT             = _env_int("RDS_PORT", 3306)
RDS_USER             = _env("RDS_USER")
RDS_PASSWORD         = _env("RDS_PASSWORD")
RDS_CRAWLER_DB       = _env("RDS_CRAWLER_DB")       # INSERT 대상 (t_crawl_url)
RDS_TRENDTRACKER_DB  = _env("RDS_TRENDTRACKER_DB", "trendtracker")  # SELECT 대상 (t_di_config_v1)

# trendtracker(t_di_config_v1)는 crawlerdb와 다른 DB 서버에 있을 수 있어 접속 정보를
# 별도로 둔다. 값을 지정하지 않으면 위 crawlerdb 접속 정보를 그대로 쓰므로, 두 스키마가
# 같은 서버에 있는 기존 배포는 .env 변경 없이 그대로 동작한다.
# SSH 터널은 crawlerdb와 같은 bastion(TUNNEL_SSH_HOST/PORT/USER/KEY_PATH)과 같은 로컬
# 포워딩 포트(TUNNEL_LOCAL_PORT)를 그대로 공유한다 — db_context()/trendtracker_db_context()
# 는 항상 순차적으로만 쓰이고(하나가 끝나야 다음이 열림) 절대 동시에 열리지 않으므로
# 로컬 포트를 나눌 필요가 없다.
RDS_TRENDTRACKER_HOST          = _env("RDS_TRENDTRACKER_HOST", RDS_HOST)
RDS_TRENDTRACKER_PORT          = _env_int("RDS_TRENDTRACKER_PORT", RDS_PORT)
RDS_TRENDTRACKER_USER          = _env("RDS_TRENDTRACKER_USER", RDS_USER)
RDS_TRENDTRACKER_PASSWORD      = _env("RDS_TRENDTRACKER_PASSWORD", RDS_PASSWORD)

# Worker
WORKER_ID = _env("WORKER_ID", "rescrape-1")

# Solr 접속 모드
# [직접 모드] SOLR_DIRECT_ENABLED=true → SOLR_URL 과 아래 파라미터를 그대로 사용한다.
# [DB 조회 모드] SOLR_DIRECT_ENABLED=false → DI_* 조건으로 trendtracker.t_di_config_v1 에서
#   solr_url/filter_query만 가져온다. query/timeperiod/max_result_cnt는 이 모드에서도
#   항상 아래 SOLR_QUERY/SLIDING_WINDOW_MINUTES/SOLR_MAX_DOCS(env) 값을 그대로 쓴다.
SOLR_DIRECT_ENABLED    = _env_bool("SOLR_DIRECT_ENABLED")
SOLR_URL               = _env("SOLR_URL", "")
SOLR_QUERY             = _env("SOLR_QUERY", "").strip() or "*:*"  # 직접 모드 q 파라미터
SOLR_FILTER_QUERY      = _env("SOLR_FILTER_QUERY", "")    # 직접 모드 fq 파라미터
SLIDING_WINDOW_MINUTES = _env_int("SLIDING_WINDOW_MINUTES", 30)  # 직접 모드 슬라이딩 윈도우(분)
SOLR_MAX_DOCS          = _env_int("SOLR_MAX_DOCS", 1000)  # 직접 모드 최대 조회 수

# URL contains 패턴 필터 (쉼표 구분, OR 결합) — 직접/DB 모드 공통.
# 각 패턴은 url:*{패턴}* wildcard fq 로 변환된다. 미설정 시 필터 없음.
SOLR_RESCRAPE_URL_CONTAINS = _env("SOLR_RESCRAPE_URL_CONTAINS", "")

# DB 조회 모드에서 워터마크(last_synced_at)가 슬라이딩 윈도우보다 오래 뒤처졌을 때
# 최대 이만큼(분)까지만 과거로 확장해서 조회한다 — 장기 다운타임 후 한 번에
# 너무 넓은 범위를 긁어오는 것을 방지하는 상한. 직접 모드에는 적용되지 않는다
# (워터마크를 저장할 DB 행이 없어 순수 슬라이딩 윈도우로만 동작).
SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES = _env_int(
    "SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES", 1440
)

# 워터마크 로컬 파일 저장 경로 (DB 조회 모드 전용). trendtracker.t_di_config_v1 은
# 외부 소유 테이블이라 컬럼을 추가할 수 없어 DB 대신 파일로 저장한다.
# 컨테이너 재시작에도 유지하려면 호스트 볼륨으로 마운트할 것.
WATERMARK_DIR = _env("WATERMARK_DIR", "./watermark")

# DB 조회 모드 조건 (SOLR_URL 비워둔 경우에만 사용)
DI_TNT_ID       = _env("DI_TNT_ID", "")
DI_PROJECT_ID   = _env("DI_PROJECT_ID", "")
DI_SERVER_IP    = _env("DI_SERVER_IP", "")
HTTP_VERIFY_SSL = _env_bool("HTTP_VERIFY_SSL", True)

# Solr 요청 1회당 rows (직접/DB 모드 공통)
SOLR_QUERY_BATCH_SIZE = _env_int("SOLR_QUERY_BATCH_SIZE", 100)

# 재수집 source_type 고정값 — Solr 에서 오는 URL 임을 식별하기 위한 상수
SOLR_RESCRAPE_SOURCE_TYPE  = "SOLR_RESCRAPE"

# Dispatch
DISPATCH_INTERVAL_SECONDS = _env_int("DISPATCH_INTERVAL_SECONDS", 300)
RESCRAPE_PRIORITY         = _env_int("RESCRAPE_PRIORITY", 5)

# Logging
LOG_DIR                    = _env("LOG_DIR", "./logs")
LOG_LEVEL                  = _env("LOG_LEVEL", "INFO")
LOG_ROTATION               = _env("LOG_ROTATION", "daily")
LOG_RETAIN_DAYS            = _env_int("LOG_RETAIN_DAYS", 30)
LOG_BACKUP_COUNT           = _env_int("LOG_BACKUP_COUNT", 10)
HEARTBEAT_INTERVAL_SECONDS = _env_int("HEARTBEAT_INTERVAL_SECONDS", 60)


# ---------------------------------------------------------------------------
# 시작 시 검증
# ---------------------------------------------------------------------------

_REQUIRED_ALWAYS   = ["RDS_HOST", "RDS_USER", "RDS_PASSWORD", "RDS_CRAWLER_DB"]
_REQUIRED_TUNNEL   = ["TUNNEL_SSH_HOST", "TUNNEL_SSH_KEY_PATH"]
_REQUIRED_DB_MODE  = ["DI_TNT_ID", "DI_PROJECT_ID", "DI_SERVER_IP"]


def validate() -> None:
    """
    필수 환경변수를 일괄 검증한다.
    누락 항목이 있으면 목록을 stderr 에 출력하고 sys.exit(1).
    __main__.py 에서 진입 전에 호출한다.
    """
    missing = [k for k in _REQUIRED_ALWAYS if not os.getenv(k)]

    if TUNNEL_ENABLED:
        missing += [k for k in _REQUIRED_TUNNEL if not os.getenv(k)]

    # DB 조회 모드 → DI_* 세 값이 필수
    if not SOLR_DIRECT_ENABLED:
        missing += [k for k in _REQUIRED_DB_MODE if not os.getenv(k)]

    if not missing:
        return

    print("ERROR: 다음 필수 환경변수가 설정되지 않았습니다:", file=sys.stderr)
    for key in missing:
        print(f"  - {key}", file=sys.stderr)
    print("  .env 파일 또는 환경변수를 확인하세요.", file=sys.stderr)
    sys.exit(1)
