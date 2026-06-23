"""
SQLAlchemy Core 테이블 정의 — trendtracker 스키마.
ORM 매핑 없이 MetaData + Table 로만 정의해 Alembic autogenerate에 사용.
"""

from sqlalchemy import (
    MetaData, Table, Column,
    BigInteger, String, Enum, DateTime,
    UniqueConstraint,
)
from sqlalchemy.sql import func

metadata = MetaData()

# ---------------------------------------------------------------------------
# di_config_v1  (Solr DI 서버 설정)
# ---------------------------------------------------------------------------
di_config = Table(
    "t_di_config_v1",
    metadata,
    Column("id",            BigInteger,   primary_key=True, autoincrement=True),
    Column("tnt_id",        String(50),   nullable=False,   comment="테넌트 ID"),
    Column("project_id",    String(50),   nullable=False,   comment="프로젝트 ID"),
    Column("di_server_ip",  String(50),   nullable=False,   comment="DI 서버 IP"),
    Column("solr_url",      String(500),  nullable=False,   comment="Solr 쿼리 URL"),
    Column("filter_query",  String(500),  nullable=True,    comment="추가 fq 파라미터 (NULL = 없음)"),
    Column("use_yn",        Enum("Y", "N"), nullable=False, server_default="Y", comment="사용 여부"),
    Column("created_at",    DateTime,     nullable=False,   server_default=func.now()),
    Column("updated_at",    DateTime,     nullable=False,   server_default=func.now(),
           onupdate=func.now()),
    UniqueConstraint("tnt_id", "project_id", "di_server_ip", name="uq_di_config"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_unicode_ci",
    comment="Solr DI 서버 설정 — rescrape-dispatcher DB 조회 모드",
)
