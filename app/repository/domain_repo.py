"""
domain 테이블 조회 전용 접근.

t_domain 은 crawlerdb-migrations(별도 저장소)가 스키마를 소유하는 공유 테이블이다.
excluded=1 인 host 는 수집 파이프라인에서 완전히 제외한다는 의미이므로,
Solr 에서 가져온 신규 URL 이라도 t_crawl_url 에 투입하기 전에 걸러낸다.
"""

from __future__ import annotations

from sqlalchemy import Engine, bindparam, text


class DomainRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_excluded_hosts(self, hosts: list[str]) -> set[str]:
        """주어진 host 목록 중 excluded=1 인 것만 골라 반환한다."""
        if not hosts:
            return set()

        stmt = text(
            "SELECT host FROM t_domain WHERE excluded = 1 AND host IN :hosts"
        ).bindparams(bindparam("hosts", expanding=True))

        with self._engine.begin() as conn:
            rows = conn.execute(stmt, {"hosts": list(set(hosts))}).fetchall()

        return {row.host for row in rows}
