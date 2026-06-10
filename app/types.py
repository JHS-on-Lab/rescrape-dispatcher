"""
핵심 데이터 타입.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SolrDocument:
    """Solr 에서 조회된 문서 한 건."""
    id: str           # url_hash (Solr 문서 id)
    url: str
    portal_type: str


@dataclass
class DispatchStats:
    """디스패치 1사이클 결과 집계."""
    total_fetched:   int   # Solr 에서 가져온 총 URL 수
    rows_affected:   int   # DB INSERT/UPDATE 영향 행 수 (new=1, requeue=2, skip=0)
    cycle_seconds:   float # 사이클 소요 시간 (초)
