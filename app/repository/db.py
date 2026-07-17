"""
DB 연결 관리: SSH 터널(선택) + SQLAlchemy 엔진.

접속 대상이 둘 있다:
  - db_context()             : crawlerdb (t_crawl_url) — discovery-worker 등과 공유
  - trendtracker_db_context() : trendtracker (t_di_config_v1) — DB 조회 모드 전용

두 스키마가 서로 다른 DB 서버에 있을 수 있어 엔진을 분리했다. SSH 터널을 쓰는
경우 bastion과 로컬 포워딩 포트(TUNNEL_SSH_HOST/PORT/USER/KEY_PATH,
TUNNEL_LOCAL_PORT) 전부를 두 접속이 공유한다 — db_context()와
trendtracker_db_context()는 코드 어디에서도 동시에(nested로) 열리지 않고 항상
하나가 닫힌 뒤 다음이 열리므로, 로컬 포트를 나눌 필요가 없다.

사용법:
    with db_context() as engine:
        with engine.begin() as conn:
            conn.execute(...)

터널이 활성화된 경우 context 종료 시 터널도 함께 닫힌다.
"""

from contextlib import contextmanager
from sqlalchemy import create_engine, Engine
from sqlalchemy.engine import URL
from sshtunnel import SSHTunnelForwarder

from app import config


def _dsn(host: str, port: int, user: str, password: str, database: str) -> URL:
    # URL.create() 는 username/password 를 자동으로 URL-encoding 한다.
    # f-string 조립은 비밀번호에 '@' 같은 특수문자가 있으면 DSN 파싱 자체가 깨진다.
    return URL.create(
        "mysql+pymysql",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
        query={"charset": "utf8mb4"},
    )


@contextmanager
def _open(rds_host: str, rds_port: int, rds_user: str, rds_password: str,
          database: str, tunnel_local_port: int):
    """SSH 터널(옵션) + SQLAlchemy 엔진을 열고 닫는 공용 context manager.
    db_context()/trendtracker_db_context() 가 각자의 접속 정보로 이를 감싼다."""
    tunnel: SSHTunnelForwarder | None = None
    engine: Engine | None = None

    try:
        if config.TUNNEL_ENABLED:
            tunnel = SSHTunnelForwarder(
                (config.TUNNEL_SSH_HOST, config.TUNNEL_SSH_PORT),
                ssh_username=config.TUNNEL_SSH_USER,
                ssh_pkey=config.TUNNEL_SSH_KEY_PATH,
                remote_bind_address=(rds_host, rds_port),
                local_bind_address=("127.0.0.1", tunnel_local_port),
            )
            tunnel.start()
            dsn = _dsn("127.0.0.1", tunnel_local_port, rds_user, rds_password, database)
        else:
            dsn = _dsn(rds_host, rds_port, rds_user, rds_password, database)

        engine = create_engine(
            dsn,
            pool_pre_ping=True,
            pool_recycle=1800,
            echo=False,
        )
        yield engine

    finally:
        if engine:
            engine.dispose()
        if tunnel and tunnel.is_active:
            tunnel.stop()


@contextmanager
def db_context():
    """crawlerdb(t_crawl_url) 접속. SSH 터널(옵션) + SQLAlchemy 엔진을 열고 닫는다."""
    with _open(
        config.RDS_HOST, config.RDS_PORT, config.RDS_USER, config.RDS_PASSWORD,
        config.RDS_CRAWLER_DB, config.TUNNEL_LOCAL_PORT,
    ) as engine:
        yield engine


@contextmanager
def trendtracker_db_context():
    """trendtracker(t_di_config_v1) 접속. crawlerdb와 다른 서버일 수 있어 별도
    엔진을 연다 — RDS_TRENDTRACKER_* 로 지정하지 않으면 crawlerdb와 동일한 접속
    정보로 폴백한다(app/config.py 참고)."""
    with _open(
        config.RDS_TRENDTRACKER_HOST, config.RDS_TRENDTRACKER_PORT,
        config.RDS_TRENDTRACKER_USER, config.RDS_TRENDTRACKER_PASSWORD,
        config.RDS_TRENDTRACKER_DB, config.TUNNEL_TRENDTRACKER_LOCAL_PORT,
    ) as engine:
        yield engine
