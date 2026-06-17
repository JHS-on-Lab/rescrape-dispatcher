"""
디스패처: Solr 에서 신규 URL 을 조회해 t_article_url 에 투입한다.

슬라이딩 윈도우 방식으로 동작한다:
  매 사이클마다 tstamp 기준 최근 SLIDING_WINDOW_MINUTES 분 이내 문서를 조회해
  t_article_url 에 INSERT IGNORE 로 신규 URL 만 삽입한다.
  이미 존재하는 URL 은 skip 된다.

  상태 저장 없음. 파일도 테이블도 필요 없다.
  멀티 인스턴스로 실행해도 INSERT IGNORE 멱등성으로 안전하다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

from app import config
from app.repository.di_config_repo import DiConfigRepo
from app.repository.db import db_context
from app.repository.article_url_repo import ArticleUrlRepo
from app.solr.client import SolrClient
from app.types import DispatchStats

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

_ERROR_SLEEP_SEC = 30


def run_dispatch_loop(worker_id: str) -> None:
    """디스패처 메인 루프. __main__.py 에서 호출."""
    logger.info(
        "dispatch loop started",
        extra={"phase": "startup", "worker_id": worker_id},
    )
    logger.info(
        f"config: query='{config.SOLR_RESCRAPE_QUERY}' "
        f"window={config.SLIDING_WINDOW_MINUTES}min "
        f"runtime_key='{config.SOLR_RESCRAPE_RUNTIME_KEY}' "
        f"url_contains='{config.SOLR_RESCRAPE_URL_CONTAINS}' "
        f"max_docs={config.SOLR_RESCRAPE_MAX_DOCS} "
        f"interval={config.DISPATCH_INTERVAL_SECONDS}s",
        extra={"phase": "startup", "worker_id": worker_id},
    )

    heartbeat_interval = config.HEARTBEAT_INTERVAL_SECONDS
    last_heartbeat = time.monotonic()
    cycle = 0

    with db_context() as engine:
        solr_url = _resolve_solr_url(engine)
        solr = SolrClient(solr_url)
        url_repo = ArticleUrlRepo(engine)

        try:
            while True:
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    logger.info(
                        f"heartbeat cycle={cycle}",
                        extra={"phase": "heartbeat", "worker_id": worker_id},
                    )
                    last_heartbeat = now

                stats = _run_one_cycle(url_repo, solr, worker_id)
                cycle += 1

                logger.info(
                    f"cycle={cycle} fetched={stats.total_fetched} "
                    f"inserted={stats.inserted} "
                    f"elapsed={stats.cycle_seconds:.1f}s "
                    f"next_run={_next_run_kst(config.DISPATCH_INTERVAL_SECONDS)}",
                    extra={"phase": "cycle_done", "worker_id": worker_id},
                )

                time.sleep(config.DISPATCH_INTERVAL_SECONDS)
        finally:
            solr.close()


def _run_one_cycle(
    url_repo: ArticleUrlRepo,
    solr: SolrClient,
    worker_id: str,
) -> DispatchStats:
    """
    Solr 조회 → DB insert 1사이클.
    예외 발생 시 ERROR 로그를 남기고 DispatchStats(0, 0) 를 반환해 루프를 유지한다.
    """
    started = time.monotonic()

    try:
        docs = solr.query_rescrape_candidates()
        logger.info(
            f"solr fetched={len(docs)}",
            extra={"phase": "solr_fetch", "worker_id": worker_id},
        )
    except Exception:
        elapsed = time.monotonic() - started
        logger.exception(
            "Solr query failed",
            extra={"phase": "solr_fetch", "worker_id": worker_id},
        )
        time.sleep(_ERROR_SLEEP_SEC)
        return DispatchStats(total_fetched=0, inserted=0, cycle_seconds=elapsed)

    if not docs:
        return DispatchStats(
            total_fetched=0,
            inserted=0,
            cycle_seconds=time.monotonic() - started,
        )

    try:
        total, inserted = url_repo.bulk_insert_new(docs, priority=config.RESCRAPE_PRIORITY)
        logger.info(
            f"db insert total={total} inserted={inserted} skipped={total - inserted}",
            extra={"phase": "db_insert", "worker_id": worker_id},
        )
    except Exception:
        elapsed = time.monotonic() - started
        logger.exception(
            "DB insert failed",
            extra={"phase": "db_insert", "worker_id": worker_id},
        )
        time.sleep(_ERROR_SLEEP_SEC)
        return DispatchStats(total_fetched=len(docs), inserted=0, cycle_seconds=elapsed)

    return DispatchStats(
        total_fetched=len(docs),
        inserted=inserted,
        cycle_seconds=time.monotonic() - started,
    )


def _resolve_solr_url(engine) -> str:
    """trendtracker.t_di_config_v1 에서 Solr URL 을 가져온다."""
    url = DiConfigRepo(engine).get_solr_url()
    if not url:
        raise RuntimeError(
            f"trendtracker.t_di_config_v1 에서 "
            f"tnt_id='{config.DI_TNT_ID}' project_id='{config.DI_PROJECT_ID}' "
            f"di_server_ip='{config.DI_SERVER_IP}' 에 해당하는 행을 찾을 수 없습니다."
        )
    return url


def _next_run_kst(interval_sec: int) -> str:
    next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_sec)
    return next_run.astimezone(KST).strftime("%H:%M KST")
