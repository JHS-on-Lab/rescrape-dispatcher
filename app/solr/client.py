"""
Solr 조회 클라이언트 — 재수집 대상 URL 목록을 가져온다.

슬라이딩 윈도우 방식으로 동작한다:
  매 사이클마다 tstamp 기준 최근 N분(default_timeperiod) 이내 문서를 조회한다.
  DB 조회 모드에서는 워터마크(last_synced_at)가 슬라이딩 윈도우보다 뒤처져 있으면
  (컨테이너 다운타임 등) 시작 시각을 워터마크까지 확장한다 — 단
  SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES 를 넘게 과거로는 확장하지 않는다.
  직접 모드는 워터마크를 저장할 DB 행이 없어 항상 순수 슬라이딩 윈도우로 동작한다.
  윈도우 내 중복 조회 문서는 INSERT IGNORE 로 무해하게 처리된다.

Solr cursor 기반 페이지네이션:
  - 첫 요청: cursorMark=*
  - 다음 요청: 이전 응답의 nextCursorMark 사용
  - 종료 조건: nextCursorMark == 이전 cursorMark (결과 소진)
  - sort: tstamp asc, id asc (시간순 — 슬라이딩 윈도우에 필수)

적용되는 fq (AND 결합):
  tstamp:[start TO NOW]            — 슬라이딩 윈도우(+워터마크 확장)
  {filter_query}                   — t_di_config_v1.filter_query (설정 시)
  {url_contains}                   — SOLR_RESCRAPE_URL_CONTAINS (설정 시, url:*패턴* OR 결합)

q 파라미터: 환경변수 SOLR_QUERY (미설정 시 *:*)

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


def _build_url_contains_fq(patterns_raw: str) -> str | None:
    """SOLR_RESCRAPE_URL_CONTAINS 를 Solr fq 로 변환한다.

    쉼표로 구분된 각 패턴을 url:*{패턴}* wildcard 로 바꾸고, 여러 개면 OR 로 묶는다.
    설정 안 됐으면 None.
    """
    patterns = [p.strip() for p in patterns_raw.split(",") if p.strip()]
    if not patterns:
        return None
    clauses = [f"url:*{p}*" for p in patterns]
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " OR ".join(clauses) + ")"


class SolrClient:
    def __init__(self, di_config: DiConfig) -> None:
        self._base_url      = di_config.solr_url.rstrip("/")
        self._query         = di_config.query
        self._filter_query  = di_config.filter_query
        self._window_min    = di_config.timeperiod
        self._max_docs      = di_config.max_result_cnt
        self._last_synced_at = di_config.last_synced_at
        self._max_lookback_min = config.SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES
        self._batch         = config.SOLR_QUERY_BATCH_SIZE
        self._http          = httpx.Client(timeout=30.0, verify=config.HTTP_VERIFY_SSL)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def query_rescrape_candidates(self) -> tuple[list[SolrDocument], str, datetime]:
        """
        슬라이딩 윈도우(+워터마크) 조건으로 Solr 를 조회해 신규 URL 목록,
        조회 범위 문자열, 이번 조회의 상한 시각(윈도우 끝 = 다음 워터마크 후보)을 반환한다.
        max_result_cnt 를 초과하면 그 시점에서 중단한다.
        """
        results: list[SolrDocument] = []
        cursor = "*"
        fq, time_range, window_end = self._build_fq()

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

        return results, time_range, window_end

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # 내부 구현
    # ------------------------------------------------------------------

    def _build_fq(self) -> tuple[list[str], str, datetime]:
        """활성화된 fq 목록, 조회 시간 범위 문자열, 조회 상한 시각(윈도우 끝)을 반환한다."""
        now_utc = datetime.now(timezone.utc)
        default_start = now_utc - timedelta(minutes=self._window_min)

        start_utc = default_start
        if self._last_synced_at is not None and self._last_synced_at < default_start:
            # 워터마크가 슬라이딩 윈도우보다 뒤처짐(다운타임 등) — 상한(lookback cap)까지만 확장.
            lookback_floor = now_utc - timedelta(minutes=self._max_lookback_min)
            start_utc = max(self._last_synced_at, lookback_floor)

        ts_now   = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_start = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        fq: list[str] = [f"tstamp:[{ts_start} TO {ts_now}]"]
        if self._filter_query:
            fq.append(self._filter_query)

        url_contains_fq = _build_url_contains_fq(config.SOLR_RESCRAPE_URL_CONTAINS)
        if url_contains_fq:
            fq.append(url_contains_fq)

        return fq, f"{ts_start} ~ {ts_now}", now_utc
