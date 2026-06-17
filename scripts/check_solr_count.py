"""
Solr 조회 건수 확인 스크립트.

실행:
  python scripts/check_solr_count.py             # 슬라이딩 윈도우 적용
  python scripts/check_solr_count.py --no-window # tstamp 필터 없이 전체 조회

접속 모드 (.env 설정에 따라 자동 선택):
  SOLR_DIRECT_ENABLED=true  → SOLR_URL 과 env 파라미터로 직접 접속
  SOLR_DIRECT_ENABLED=false → DI_* 조건으로 trendtracker.t_di_config_v1 조회 후 접속

filter_query 예시:
  crawl_runtime_key:"127.0.0.1_bc_kr_cns_pr"

적용되는 fq:
  tstamp:[NOW-{timeperiod}MINUTES TO NOW]   — 슬라이딩 윈도우
  {filter_query}                            — filter_query (설정 시)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
from datetime import datetime, timezone, timedelta

import httpx

from app import config
from app.types import DiConfig


def _resolve_di_config() -> DiConfig:
    if config.SOLR_DIRECT_ENABLED:
        if not config.SOLR_URL:
            print("[오류] SOLR_DIRECT_ENABLED=true 이지만 SOLR_URL 이 설정되지 않았습니다.")
            sys.exit(1)
        print("[모드] 직접 접속 (SOLR_DIRECT_ENABLED=true)")
        return DiConfig(
            solr_url=config.SOLR_URL,
            query=config.SOLR_QUERY,
            filter_query=config.SOLR_FILTER_QUERY,
            timeperiod=config.SLIDING_WINDOW_MINUTES,
            max_result_cnt=config.SOLR_MAX_DOCS,
        )

    if not (config.DI_TNT_ID and config.DI_PROJECT_ID and config.DI_SERVER_IP):
        print("[오류] DI_TNT_ID / DI_PROJECT_ID / DI_SERVER_IP 를 .env 에 설정하세요.")
        sys.exit(1)

    print(
        f"[모드] DB 조회 "
        f"(tnt_id={config.DI_TNT_ID} project_id={config.DI_PROJECT_ID} di_server_ip={config.DI_SERVER_IP})"
    )
    from app.repository.db import db_context
    from app.repository.di_config_repo import DiConfigRepo

    with db_context() as engine:
        di_config = DiConfigRepo(engine).get_config()

    if not di_config:
        print("[오류] t_di_config_v1 에서 조건에 맞는 행이 없거나 use_yn='N' 입니다.")
        sys.exit(1)

    return di_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-window", action="store_true", help="tstamp 슬라이딩 윈도우 필터 제외 (전체 기간 조회)")
    args = parser.parse_args()

    di_config = _resolve_di_config()

    solr_url = di_config.solr_url.rstrip("/")
    print(f"Solr URL     : {solr_url}")
    print(f"q            : {di_config.query}")
    print(f"filter_query : {di_config.filter_query or '(없음)'}")

    fq = []
    if args.no_window:
        print(f"window       : 미적용 (--no-window)")
    else:
        now_utc   = datetime.now(timezone.utc)
        start_utc = now_utc - timedelta(minutes=di_config.timeperiod)
        ts_now   = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_start = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        fq.append(f"tstamp:[{ts_start} TO {ts_now}]")
        print(f"window       : {di_config.timeperiod}분 ({ts_start} ~ {ts_now})")
    if di_config.filter_query:
        fq.append(di_config.filter_query)
    print()

    print("Solr 조회 중...")
    try:
        params = {"q": di_config.query, "rows": 0, "wt": "json"}
        if fq:
            params["fq"] = fq
        resp = httpx.get(
            f"{solr_url}/select",
            params=params,
            timeout=10,
            verify=config.HTTP_VERIFY_SSL,
        )
        resp.raise_for_status()
        num_found = resp.json().get("response", {}).get("numFound", 0)
    except Exception as e:
        print(f"[오류] Solr 조회 실패: {e}")
        sys.exit(1)

    print(f"조회된 문서 수: {num_found:,}건")


if __name__ == "__main__":
    main()
