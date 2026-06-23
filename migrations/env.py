"""
Alembic env.py — SSH 터널을 열고 trendtracker 스키마 RDS에 연결한 뒤 마이그레이션을 실행한다.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, Engine
from sshtunnel import SSHTunnelForwarder

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import config as app_config

alembic_config = context.config
if alembic_config.config_file_name:
    fileConfig(alembic_config.config_file_name)

from migrations.models import metadata
target_metadata = metadata


def _dsn(host: str, port: int) -> str:
    return (
        f"mysql+pymysql://{app_config.RDS_USER}:{app_config.RDS_PASSWORD}"
        f"@{host}:{port}/{app_config.RDS_TRENDTRACKER_DB}"
        f"?charset=utf8mb4"
    )


@contextmanager
def trendtracker_db_context():
    """SSH 터널(옵션) + trendtracker 스키마 SQLAlchemy 엔진."""
    tunnel: SSHTunnelForwarder | None = None
    engine: Engine | None = None

    try:
        if app_config.TUNNEL_ENABLED:
            tunnel = SSHTunnelForwarder(
                (app_config.TUNNEL_SSH_HOST, app_config.TUNNEL_SSH_PORT),
                ssh_username=app_config.TUNNEL_SSH_USER,
                ssh_pkey=app_config.TUNNEL_SSH_KEY_PATH,
                remote_bind_address=(app_config.RDS_HOST, app_config.RDS_PORT),
                local_bind_address=("127.0.0.1", app_config.TUNNEL_LOCAL_PORT),
            )
            tunnel.start()
            dsn = _dsn("127.0.0.1", app_config.TUNNEL_LOCAL_PORT)
        else:
            dsn = _dsn(app_config.RDS_HOST, app_config.RDS_PORT)

        engine = create_engine(dsn, pool_pre_ping=True, pool_recycle=1800, echo=False)
        yield engine

    finally:
        if engine:
            engine.dispose()
        if tunnel and tunnel.is_active:
            tunnel.stop()


def run_migrations_online() -> None:
    """SSH 터널(옵션)을 열고 온라인 마이그레이션 실행."""
    with trendtracker_db_context() as engine:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()


def run_migrations_offline() -> None:
    """오프라인 모드(SQL 파일 출력)."""
    if app_config.TUNNEL_ENABLED:
        dsn = _dsn("127.0.0.1", app_config.TUNNEL_LOCAL_PORT)
    else:
        dsn = _dsn(app_config.RDS_HOST, app_config.RDS_PORT)

    context.configure(
        url=dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
