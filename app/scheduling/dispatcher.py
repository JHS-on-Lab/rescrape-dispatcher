"""
디스패처: Solr 에서 신규 URL 을 조회해 t_crawl_url 에 투입한다.

슬라이딩 윈도우 방식으로 동작한다:
  매 사이클마다 tstamp 기준 최근 SLIDING_WINDOW_MINUTES 분 이내 문서를 조회해
  t_crawl_url 에 INSERT IGNORE 로 신규 URL 만 삽입한다.
  이미 존재하는 URL 은 skip 된다.

  DB 조회 모드는 워터마크(app/repository/watermark_store.py, 로컬 파일)를 같이 써서
  다운타임으로 슬라이딩 윈도우보다 뒤처진 구간을 상한 내에서 따라잡는다.
  직접 모드는 워터마크 파일이 없어 상태 저장 없이 순수 슬라이딩 윈도우로 동작한다.

  멀티 인스턴스로 실행해도 INSERT IGNORE 멱등성으로 안전하다. 워터마크 파일은
  WORKER_ID 별로 분리되므로 같은 DI 설정을 공유하는 여러 컨테이너가 워터마크
  디렉터리를 공유 마운트해도 서로의 파일을 건드리지 않는다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

from app import config
from app.repository import watermark_store
from app.repository.di_config_repo import DiConfigRepo
from app.types import DiConfig, DispatchStats
from app.repository.db import db_context
from app.repository.crawl_url_repo import CrawlUrlRepo
from app.solr.client import SolrClient

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

_ERROR_SLEEP_SEC = 30


def run_dispatch_loop(worker_id: str) -> None:
    """디스패처 메인 루프. __main__.py 에서 호출."""
    logger.info(
        "dispatch loop started",
        extra={"phase": "startup", "worker_id": worker_id},
    )
    if config.SOLR_DIRECT_ENABLED:
        logger.info(
            f"solr 모드: 직접 접속 (SOLR_DIRECT_ENABLED=true) interval={config.DISPATCH_INTERVAL_SECONDS}s",
            extra={"phase": "startup", "worker_id": worker_id},
        )
    else:
        logger.info(
            f"solr 모드: DB 조회 — tnt_id='{config.DI_TNT_ID}' "
            f"project_id='{config.DI_PROJECT_ID}' "
            f"di_server_ip='{config.DI_SERVER_IP}' "
            f"interval={config.DISPATCH_INTERVAL_SECONDS}s",
            extra={"phase": "startup", "worker_id": worker_id},
        )

    heartbeat_interval = config.HEARTBEAT_INTERVAL_SECONDS
    last_heartbeat = time.monotonic()
    cycle = 0

    with db_context() as engine:
        di_config = resolve_di_config(engine, worker_id)
        logger.info(
            f"solr: url='{di_config.solr_url}' "
            f"q='{di_config.query}' "
            f"filter_query='{di_config.filter_query or '(없음)'}' "
            f"window={di_config.timeperiod}min "
            f"max={di_config.max_result_cnt} "
            f"url_contains='{config.SOLR_RESCRAPE_URL_CONTAINS or '(없음)'}' "
            f"watermark={di_config.last_synced_at or '(없음, 순수 슬라이딩 윈도우)'}",
            extra={"phase": "startup", "worker_id": worker_id},
        )
        solr = SolrClient(di_config)
        url_repo = CrawlUrlRepo(engine)

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
    url_repo: CrawlUrlRepo,
    solr: SolrClient,
    worker_id: str,
) -> DispatchStats:
    """
    Solr 조회 → DB insert 1사이클.
    예외 발생 시 ERROR 로그를 남기고 DispatchStats(0, 0) 를 반환해 루프를 유지한다.
    """
    started = time.monotonic()

    try:
        docs, time_range, window_end = solr.query_rescrape_candidates()
        logger.info(
            f"solr fetched={len(docs)} range={time_range}",
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
        _advance_watermark_if_db_mode(worker_id, window_end)
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

    # Solr 조회 + DB insert 가 둘 다 성공한 경우에만 워터마크를 전진시킨다 —
    # 둘 중 하나라도 실패하면 다음 사이클에서 같은 구간을 다시 조회해야 하기 때문.
    _advance_watermark_if_db_mode(worker_id, window_end)

    return DispatchStats(
        total_fetched=len(docs),
        inserted=inserted,
        cycle_seconds=time.monotonic() - started,
    )


def _advance_watermark_if_db_mode(worker_id: str, window_end: datetime) -> None:
    """DB 조회 모드일 때만 워터마크를 전진시킨다. 직접 모드는 저장할 DI 설정 행이 없어 skip."""
    if config.SOLR_DIRECT_ENABLED:
        return
    watermark_store.advance(
        config.DI_TNT_ID, config.DI_PROJECT_ID, config.DI_SERVER_IP, worker_id,
        window_end,
    )


def resolve_di_config(engine=None, worker_id: str = "") -> DiConfig:
    """직접 모드(SOLR_DIRECT_ENABLED) 또는 DB 조회 모드로 Solr 설정을 반환한다.

    직접 모드에서는 engine 불필요, 워터마크도 없음(항상 순수 슬라이딩 윈도우).
    DB 조회 모드에서는 engine 필수이고, worker_id 로 로컬 워터마크 파일을 불러온다.
    """
    if config.SOLR_DIRECT_ENABLED:
        return DiConfig(
            solr_url=config.SOLR_URL,
            query=config.SOLR_QUERY,
            filter_query=config.SOLR_FILTER_QUERY or None,
            timeperiod=config.SLIDING_WINDOW_MINUTES,
            max_result_cnt=config.SOLR_MAX_DOCS,
        )

    di_config = DiConfigRepo(engine).get_config()
    if not di_config:
        raise RuntimeError(
            f"trendtracker.t_di_config_v1 에서 "
            f"tnt_id='{config.DI_TNT_ID}' project_id='{config.DI_PROJECT_ID}' "
            f"di_server_ip='{config.DI_SERVER_IP}' 에 해당하는 행을 찾을 수 없거나 use_yn='N' 입니다."
        )
    di_config.last_synced_at = watermark_store.load(
        config.DI_TNT_ID, config.DI_PROJECT_ID, config.DI_SERVER_IP, worker_id,
    )
    return di_config


def _next_run_kst(interval_sec: int) -> str:
    next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_sec)
    return next_run.astimezone(KST).strftime("%H:%M KST")
