"""
디스패치 1회 실행 스크립트.

실행:
  python scripts/run_once.py

Solr 조회 → t_crawl_url INSERT 를 1회 수행하고 종료한다.
워커 전체 루프 없이 단건 테스트용으로 사용한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import config
from app.repository.db import db_context
from app.repository.crawl_url_repo import CrawlUrlRepo
from app.scheduling.dispatcher import resolve_di_config


def main() -> None:
    config.validate()

    with db_context() as engine:
        try:
            di_config = resolve_di_config(engine)
        except RuntimeError as e:
            print(f"[오류] {e}")
            sys.exit(1)

        mode = "직접 접속" if config.SOLR_DIRECT_ENABLED else "DB 조회"
        print(f"[모드] {mode}")
        print(f"Solr URL     : {di_config.solr_url}")
        print(f"filter_query : {di_config.filter_query or '(없음)'}")
        print(f"window       : {di_config.timeperiod}분")
        print()

        from app.solr.client import SolrClient

        solr = SolrClient(di_config)
        print("Solr 조회 중...")
        try:
            docs, time_range = solr.query_rescrape_candidates()
        except Exception as e:
            print(f"[오류] Solr 조회 실패: {e}")
            sys.exit(1)
        finally:
            solr.close()

        print(f"조회 범위     : {time_range}")
        print(f"조회된 URL 수: {len(docs)}건")

        if not docs:
            print("INSERT 대상 없음.")
            return

        print("t_crawl_url INSERT 중...")
        total, inserted = CrawlUrlRepo(engine).bulk_insert_new(
            docs, priority=config.RESCRAPE_PRIORITY
        )
        skipped = total - inserted
        print(f"  처리: {total}건 | 신규 INSERT: {inserted}건 | 중복 skip: {skipped}건")


if __name__ == "__main__":
    main()
