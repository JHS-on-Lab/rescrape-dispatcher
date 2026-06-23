"""create trendtracker t_di_config_v1

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-06-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "t_di_config_v1",
        sa.Column("id",           sa.BigInteger(),                           nullable=False, autoincrement=True),
        sa.Column("tnt_id",       sa.String(50),                             nullable=False, comment="테넌트 ID"),
        sa.Column("project_id",   sa.String(50),                             nullable=False, comment="프로젝트 ID"),
        sa.Column("di_server_ip", sa.String(50),                             nullable=False, comment="DI 서버 IP"),
        sa.Column("solr_url",     sa.String(500),                            nullable=False, comment="Solr 쿼리 URL"),
        sa.Column("filter_query", sa.String(500),                            nullable=True,  comment="추가 fq 파라미터 (NULL = 없음)"),
        sa.Column("use_yn",       sa.Enum("Y", "N"),                         nullable=False, server_default="Y", comment="사용 여부"),
        sa.Column("created_at",   sa.DateTime(),                             nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at",   sa.DateTime(),                             nullable=False, server_default=sa.text("now()"),
                  comment=""),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tnt_id", "project_id", "di_server_ip", name="uq_di_config"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
        comment="Solr DI 서버 설정 — rescrape-dispatcher DB 조회 모드",
    )


def downgrade() -> None:
    op.drop_table("t_di_config_v1")
