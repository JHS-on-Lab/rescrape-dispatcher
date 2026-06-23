"""
로깅 골격.

스트림 분리:
  {log_name}.log        — 정상 동작·진행·하트비트 (INFO 이상)
  {log_name}-error.log  — WARNING 이상만. "왜 멈췄나"를 한 곳에서 본다.

포맷:
  {ts} {level} [component] worker phase solr_docs db_affected msg
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from app import config

_initialized = False


class _MergingAdapter(logging.LoggerAdapter):
    """LoggerAdapter 기본 구현은 extra를 덮어쓰는 버그가 있어 병합으로 교체."""

    def process(self, msg, kwargs):
        kwargs["extra"] = {**self.extra, **kwargs.get("extra", {})}
        return msg, kwargs


def setup(component: str, worker_id: str | None = None, log_name: str = "rescrape") -> logging.Logger:
    """
    로깅을 초기화하고 component 전용 Logger를 반환한다.
    프로세스당 한 번만 실제 초기화하고 이후 호출은 Logger만 반환한다.
    """
    global _initialized

    log_dir = Path(config.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    if not _initialized:
        _configure_root(log_dir, log_name)
        _initialized = True

    logger = logging.getLogger(component)
    if worker_id:
        logger = _MergingAdapter(logger, {"worker_id": worker_id, "component": component})  # type: ignore[assignment]
    return logger


# ---------------------------------------------------------------------------
# 내부 구현
# ---------------------------------------------------------------------------

class _ContextFilter(logging.Filter):
    """필드가 없을 때 기본값을 채워준다."""

    _DEFAULTS = {
        "worker_id": "-",
        "component": "app",
        "phase":     "-",
    }

    _NOISE = (
        "discarding data",
        "DBAPI exception",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        for key, val in self._DEFAULTS.items():
            if not hasattr(record, key):
                setattr(record, key, val)
        msg = record.getMessage()
        for pattern in self._NOISE:
            if pattern in msg:
                return False
        return True


_FMT = (
    "%(asctime)s %(levelname)-5s [%(component)s] "
    "worker=%(worker_id)s phase=%(phase)s "
    "%(message)s"
)
_DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _make_rotating_handler(path: Path, level: int) -> logging.Handler:
    rotation = config.LOG_ROTATION
    if rotation == "daily":
        handler: logging.Handler = logging.handlers.TimedRotatingFileHandler(
            path, when="midnight", backupCount=config.LOG_RETAIN_DAYS, encoding="utf-8", utc=True
        )
    else:
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=100 * 1024 * 1024, backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8"
        )
    handler.setLevel(level)
    return handler


def _configure_root(log_dir: Path, log_name: str = "rescrape") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    for lib in ("httpx", "httpcore", "paramiko", "pymysql",
                "sqlalchemy.pool", "sqlalchemy.engine"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    ctx = _ContextFilter()
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    app_handler = _make_rotating_handler(log_dir / f"{log_name}.log", logging.INFO)
    app_handler.addFilter(ctx)
    app_handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    root.addHandler(app_handler)

    err_handler = _make_rotating_handler(log_dir / f"{log_name}-error.log", logging.WARNING)
    err_handler.addFilter(ctx)
    err_handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    root.addHandler(err_handler)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.addFilter(ctx)
    console.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    root.addHandler(console)
