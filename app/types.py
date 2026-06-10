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
    total_fetched: int    # Solr 에서 가져온 총 URL 수
    inserted:      int    # 실제 INSERT 된 신규 URL 수 (중복 skip 제외)
    cycle_seconds: float  # 사이클 소요 시간 (초)
