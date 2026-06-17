"""
디스패치 1회 실행 스크립트.

실행:
  python scripts/run_once.py

Solr 조회 → t_article_url INSERT 를 1회 수행하고 종료한다.
워커 전체 루프 없이 단건 테스트용으로 사용한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import config
from app.repository.db import db_context
from app.repository.article_url_repo import ArticleUrlRepo
from app.repository.di_config_repo import DiConfigRepo
from app.solr.client import SolrClient
from app.types import DiConfig


def _resolve_di_config(engine) -> DiConfig:
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

    print(
        f"[모드] DB 조회 "
        f"(tnt_id={config.DI_TNT_ID} project_id={config.DI_PROJECT_ID} di_server_ip={config.DI_SERVER_IP})"
    )
    di_config = DiConfigRepo(engine).get_config()
    if not di_config:
        print("[오류] t_di_config_v1 에서 조건에 맞는 행이 없거나 use_yn='N' 입니다.")
        sys.exit(1)
    return di_config


def main() -> None:
    with db_context() as engine:
        di_config = _resolve_di_config(engine)

        print(f"Solr URL     : {di_config.solr_url}")
        print(f"filter_query : {di_config.filter_query or '(없음)'}")
        print(f"window       : {di_config.timeperiod}분")
        print()

        solr = SolrClient(di_config)
        print("Solr 조회 중...")
        try:
            docs = solr.query_rescrape_candidates()
        except Exception as e:
            print(f"[오류] Solr 조회 실패: {e}")
            sys.exit(1)
        finally:
            solr.close()

        print(f"조회된 URL 수: {len(docs)}건")

        if not docs:
            print("INSERT 대상 없음.")
            return

        print("t_article_url INSERT 중...")
        total, inserted = ArticleUrlRepo(engine).bulk_insert_new(
            docs, priority=config.RESCRAPE_PRIORITY
        )
        skipped = total - inserted
        print(f"  처리: {total}건 | 신규 INSERT: {inserted}건 | 중복 skip: {skipped}건")


if __name__ == "__main__":
    main()
