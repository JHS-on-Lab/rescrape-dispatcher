"""
trendtracker.t_di_config_v1 조회 — Solr 접속 정보 획득.

tnt_id, project_id, di_server_ip 세 조건으로 1행을 조회하고
Solr URL 을 반환한다. 값은 .env 의 DI_TNT_ID / DI_PROJECT_ID / DI_SERVER_IP 로 지정.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from app import config


class DiConfigRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._schema = config.TRENDTRACKER_DB

    def get_solr_url(self) -> str | None:
        """
        t_di_config_v1 에서 Solr URL 을 조회한다.
        행이 없으면 None.

        TODO: 실제 solr_url 컬럼명 확인 후 'solr_url' 수정 필요
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                text(f"""
                    SELECT *
                    FROM {self._schema}.t_di_config_v1
                    WHERE tnt_id      = :tnt_id
                      AND project_id  = :project_id
                      AND di_server_ip = :di_server_ip
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

        # TODO: 실제 컬럼명으로 교체
        return row._mapping.get("solr_url")
