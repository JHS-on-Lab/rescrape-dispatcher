"""
Solr 조회 클라이언트 — 재수집 대상 URL 목록을 가져온다.

슬라이딩 윈도우 방식으로 동작한다:
  매 사이클마다 tstamp 기준 최근 N분(default_timeperiod) 이내 문서를 조회한다.
  상태 저장 없이 항상 'NOW - 윈도우'부터 'NOW'까지 고정 범위를 반복 조회한다.
  윈도우 내 중복 조회 문서는 INSERT IGNORE 로 무해하게 처리된다.

Solr cursor 기반 페이지네이션:
  - 첫 요청: cursorMark=*
  - 다음 요청: 이전 응답의 nextCursorMark 사용
  - 종료 조건: nextCursorMark == 이전 cursorMark (결과 소진)
  - sort: tstamp asc, id asc (시간순 — 슬라이딩 윈도우에 필수)

적용되는 fq (AND 결합):
  tstamp:[NOW-{N}MINUTES TO NOW]   — 슬라이딩 윈도우 (default_timeperiod)
  {filter_query}                   — t_di_config_v1.filter_query (설정 시)

q 파라미터: t_di_config_v1.custrom_query (미설정 시 *:*)

조회 필드:
  id  — 문서 id
  url — 원본 URL

source_type 은 Solr 스키마에 없으므로 조회하지 않고
config.SOLR_RESCRAPE_SOURCE_TYPE("SOLR_RESCRAPE") 상수로 고정한다.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import httpx

from app import config
from app.types import DiConfig, SolrDocument

_FL = "id,url"


class SolrClient:
    def __init__(self, di_config: DiConfig) -> None:
        self._base_url    = di_config.solr_url.rstrip("/")
        self._query       = di_config.query
        self._filter_query = di_config.filter_query
        self._window_min  = di_config.timeperiod
        self._max_docs    = di_config.max_result_cnt
        self._batch       = config.SOLR_QUERY_BATCH_SIZE
        self._http        = httpx.Client(timeout=30.0, verify=config.HTTP_VERIFY_SSL)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def query_rescrape_candidates(self) -> list[SolrDocument]:
        """
        슬라이딩 윈도우 조건으로 Solr 를 조회해 신규 URL 목록을 반환한다.
        tstamp 기준 최근 {timeperiod}분 이내 문서만 대상.
        max_result_cnt 를 초과하면 그 시점에서 중단한다.
        """
        results: list[SolrDocument] = []
        cursor = "*"
        fq = self._build_fq()

        while len(results) < self._max_docs:
            batch_size = min(self._batch, self._max_docs - len(results))
            params: dict = {
                "q":          self._query,
                "fl":         _FL,
                "rows":       batch_size,
                "sort":       "tstamp asc, id asc",
                "cursorMark": cursor,
                "wt":         "json",
            }
            if fq:
                params["fq"] = fq

            response = self._http.get(f"{self._base_url}/select", params=params)
            response.raise_for_status()

            data = response.json()
            docs = data.get("response", {}).get("docs", [])

            if not docs:
                break

            for doc in docs:
                url = doc.get("url")
                if not url:
                    continue
                results.append(SolrDocument(
                    id=doc.get("id", ""),
                    url=url,
                    source_type=config.SOLR_RESCRAPE_SOURCE_TYPE,
                ))

            next_cursor = data.get("nextCursorMark", cursor)
            if next_cursor == cursor:
                break
            cursor = next_cursor

        return results

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # 내부 구현
    # ------------------------------------------------------------------

    def _build_fq(self) -> list[str]:
        """활성화된 fq 목록을 반환한다. 각 항목은 Solr 에서 AND 로 결합된다."""
        now_utc   = datetime.now(timezone.utc)
        start_utc = now_utc - timedelta(minutes=self._window_min)
        ts_now   = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_start = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        fq: list[str] = [f"tstamp:[{ts_start} TO {ts_now}]"]
        if self._filter_query:
            fq.append(self._filter_query)
        return fq
