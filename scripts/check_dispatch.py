"""
디스패치 dry-run 확인 스크립트.

실행:
  python scripts/check_dispatch.py [--limit N]

Solr 에서 재수집 대상 URL 을 조회하고 결과를 출력한다.
DB 에는 INSERT 하지 않는다.

단계:
  1. Solr 설정 결정 (직접 모드 or DB 조회 모드)
  2. Solr 연결 후 슬라이딩 윈도우 조건으로 URL 조회
  3. 조회된 URL 목록 출력 (최대 --limit 건, 기본 20)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import config
from app.scheduling.dispatcher import resolve_di_config


def main() -> None:
    config.validate()

    parser = argparse.ArgumentParser(description="Dispatch dry-run")
    parser.add_argument("--limit", type=int, default=20, help="출력할 최대 URL 수 (기본 20)")
    args = parser.parse_args()

    try:
        if config.SOLR_DIRECT_ENABLED:
            print("[모드] 직접 접속 (SOLR_DIRECT_ENABLED=true)")
            di_config = resolve_di_config()
        else:
            print(
                f"[모드] DB 조회 "
                f"(tnt_id={config.DI_TNT_ID} project_id={config.DI_PROJECT_ID}"
                f" di_server_ip={config.DI_SERVER_IP})"
            )
            from app.repository.db import db_context
            with db_context() as engine:
                di_config = resolve_di_config(engine)
    except RuntimeError as e:
        print(f"[오류] {e}")
        sys.exit(1)

    print()
    print(f"Solr URL      : {di_config.solr_url}")
    print(f"q             : {di_config.query}")
    print(f"filter_query  : {di_config.filter_query or '(없음)'}")
    print(f"window        : {di_config.timeperiod}분")
    print(f"max_result_cnt: {di_config.max_result_cnt}")
    print()

    from app.solr.client import SolrClient

    solr = SolrClient(di_config)
    print("Solr 조회 중...")
    try:
        docs, time_range, _window_end = solr.query_rescrape_candidates()
    except Exception as e:
        print(f"[오류] Solr 조회 실패: {e}")
        sys.exit(1)
    finally:
        solr.close()

    print(f"조회 범위     : {time_range}")
    print(f"조회된 URL 수: {len(docs)}건")
    print()

    if not docs:
        print("조회된 URL 없음.")
        return

    limit = min(args.limit, len(docs))
    print(f"URL 목록 (최대 {limit}건):")
    print("-" * 80)
    for doc in docs[:limit]:
        print(f"  {doc.url}")
    if len(docs) > limit:
        print(f"  ... 외 {len(docs) - limit}건")

    print()
    print("[dry-run 완료] DB INSERT 없음.")


if __name__ == "__main__":
    main()
