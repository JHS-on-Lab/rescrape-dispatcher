"""
핵심 데이터 타입.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DiConfig:
    """trendtracker.t_di_config_v1 에서 조회한 Solr 접속·쿼리 설정."""
    solr_url:       str
    query:          str        # custrom_query (Solr q 파라미터)
    filter_query:   str | None # filter_query  (Solr fq 파라미터). 미설정 시 None
    timeperiod:     int        # default_timeperiod — 슬라이딩 윈도우 크기(분)
    max_result_cnt: int        # solr_max_result_cnt


@dataclass
class SolrDocument:
    """Solr 에서 조회된 문서 한 건."""
    id: str           # crawl_id (lookup3ycs64 기반 16자 hex)
    url: str
    source_type: str


@dataclass
class DispatchStats:
    """디스패치 1사이클 결과 집계."""
    total_fetched: int    # Solr 에서 가져온 총 URL 수
    inserted:      int    # 실제 INSERT 된 신규 URL 수 (중복 skip 제외)
    cycle_seconds: float  # 사이클 소요 시간 (초)
