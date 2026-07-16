"""
trendtracker.t_di_config_v1 조회 — Solr 접속·쿼리 설정 획득.

tnt_id, project_id, di_server_ip 세 조건으로 1행을 조회하고
DiConfig 를 반환한다. 조건값은 .env 의 DI_TNT_ID / DI_PROJECT_ID / DI_SERVER_IP.

주의: 이 테이블은 외부(trendtracker) 소유라 컬럼 추가/스키마 변경 권한이 없다.
워터마크(last_synced_at)는 이 테이블이 아니라 app/repository/watermark_store.py 가
로컬 파일로 별도 관리한다.

engine 은 반드시 app.repository.db.trendtracker_db_context() 로 연 엔진이어야
한다 — crawlerdb와 다른 서버일 수 있어 db_context() 의 엔진으로는 이 테이블에
접근할 수 없다(스키마 접두어로 우회할 수 없는 별도 서버 연결).
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from app import config
from app.types import DiConfig


class DiConfigRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_config(self) -> DiConfig | None:
        """
        t_di_config_v1 에서 Solr 설정을 조회한다.
        행이 없거나 use_yn='N' 이면 None.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT solr_url, filter_query
                    FROM t_di_config_v1
                    WHERE tnt_id       = :tnt_id
                      AND project_id   = :project_id
                      AND di_server_ip = :di_server_ip
                      AND use_yn       = 'Y'
                    LIMIT 1
                """),
                {
                    "tnt_id":       config.DI_TNT_ID,
                    "project_id":   config.DI_PROJECT_ID,
                    "di_server_ip": config.DI_SERVER_IP,
                },
            ).fetchone()

        if row is None:
            return None

        return DiConfig(
            solr_url       = (row[0] or "").strip(),
            query          = config.SOLR_QUERY,
            filter_query   = (row[1] or "").strip() or None,
            timeperiod     = config.SLIDING_WINDOW_MINUTES,
            max_result_cnt = config.SOLR_MAX_DOCS,
        )
