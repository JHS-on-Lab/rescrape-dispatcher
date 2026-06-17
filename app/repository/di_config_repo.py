"""
trendtracker.t_di_config_v1 조회 — Solr 접속·쿼리 설정 획득.

tnt_id, project_id, di_server_ip 세 조건으로 1행을 조회하고
DiConfig 를 반환한다. 조건값은 .env 의 DI_TNT_ID / DI_PROJECT_ID / DI_SERVER_IP.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from app import config
from app.types import DiConfig


class DiConfigRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._schema = config.RDS_TRENDTRACKER_DB

    def get_config(self) -> DiConfig | None:
        """
        t_di_config_v1 에서 Solr 설정을 조회한다.
        행이 없거나 use_yn='N' 이면 None.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                text(f"""
                    SELECT solr_url, custrom_query, filter_query,
                           default_timeperiod, solr_max_result_cnt
                    FROM {self._schema}.t_di_config_v1
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
            query          = "*:*",
            filter_query   = (row[2] or "").strip(),
            timeperiod     = row[3] or 30,
            max_result_cnt = row[4] or 1000,
        )
