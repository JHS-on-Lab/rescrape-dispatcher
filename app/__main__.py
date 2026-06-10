"""
rescrape-dispatcher 진입점.

실행 예:
  python -m app
  python -m app --worker-id rescrape-1

동작:
  Solr에서 설정된 조건에 맞는 URL을 주기적으로 조회해
  keyword-collector 의 t_article_url 테이블에 재수집 대상으로 등록한다.
  이후 실제 본문 추출은 keyword-collector 의 extraction worker 가 처리한다.
"""

from __future__ import annotations

import argparse
import signal
import sys

from app import logging_setup
from app import config


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="rescrape-dispatcher")
    p.add_argument("--worker-id", default=None, help="워커 식별자 (기본: 환경변수 WORKER_ID)")
    return p.parse_args()


def _handle_signal(signum: int, frame: object) -> None:
    logger = logging_setup.setup("main")
    logger.info("shutdown", extra={"phase": "shutdown", "worker_id": config.WORKER_ID})
    sys.exit(0)


def main() -> None:
    args = _parse_args()
    config.validate()

    worker_id = args.worker_id or config.WORKER_ID
    config.WORKER_ID = worker_id

    logger = logging_setup.setup("main", worker_id=worker_id, log_name=f"rescrape-{worker_id}")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    try:
        from app.scheduling.dispatcher import run_dispatch_loop
        run_dispatch_loop(worker_id=worker_id)
    except Exception:
        logger.exception(
            "unhandled exception — dispatcher stopping",
            extra={"phase": "main", "worker_id": worker_id},
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
