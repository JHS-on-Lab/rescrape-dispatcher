"""
Solr 조회 클라이언트 — 신규 URL 목록을 가져온다.

슬라이딩 윈도우 방식으로 동작한다:
  매 사이클마다 collected_at 기준 최근 SLIDING_WINDOW_MINUTES 분 이내 문서를 조회한다.
  상태 저장 없이 항상 'NOW - 윈도우'부터 'NOW'까지 고정 범위를 반복 조회한다.
  윈도우 내 중복 조회 문서는 INSERT IGNORE 로 무해하게 처리된다.

Solr cursor 기반 페이지네이션:
  - 첫 요청: cursorMark=*
  - 다음 요청: 이전 응답의 nextCursorMark 사용
  - 종료 조건: nextCursorMark == 이전 cursorMark (결과 소진)
  - sort: collected_at asc, id asc (시간순 — 슬라이딩 윈도우에 필수)

적용되는 fq (AND 결합):
  collected_at:[NOW-{N}MINUTES TO NOW]  — 슬라이딩 윈도우 (자동)
  SOLR_RESCRAPE_URL_CONTAINS            — URL contains 패턴 (설정 시)

조회 필드:
  id          — url_hash (keyword-crawler SolrSink 의 문서 id)
  url         — 원본 URL
  portal_type — 포털 유형 (NAVER_NEWS, DAUM_NEWS, etc.)
"""

from __future__ import annotations

import logging

import httpx

from app import config
from app.types import SolrDocument

logger = logging.getLogger(__name__)

_FL = "id,url,portal_type"


class SolrClient:
    def __init__(self) -> None:
        self._base_url     = config.SOLR_URL.rstrip("/")
        self._batch        = config.SOLR_QUERY_BATCH_SIZE
        self._max_docs     = config.SOLR_RESCRAPE_MAX_DOCS
        self._query        = config.SOLR_RESCRAPE_QUERY
        self._window_min   = config.SLIDING_WINDOW_MINUTES
        raw_contains       = config.SOLR_RESCRAPE_URL_CONTAINS
        self._url_contains = [p.strip() for p in raw_contains.split(",") if p.strip()] if raw_contains else []
        self._http         = httpx.Client(timeout=30.0, verify=config.HTTP_VERIFY_SSL)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def query_rescrape_candidates(self) -> list[SolrDocument]:
        """
        슬라이딩 윈도우 조건으로 Solr 를 조회해 신규 URL 목록을 반환한다.
        collected_at 기준 최근 SLIDING_WINDOW_MINUTES 분 이내 문서만 대상.
        SOLR_RESCRAPE_MAX_DOCS 를 초과하면 그 시점에서 중단한다.
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
                "sort":       "collected_at asc, id asc",
                "cursorMark": cursor,
                "wt":         "json",
            }
            if fq:
                params["fq"] = fq

            try:
                response = self._http.get(f"{self._base_url}/select", params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error(
                    f"Solr request failed: {exc}",
                    extra={"phase": "solr_query"},
                )
                raise

            data = response.json()
            docs = data.get("response", {}).get("docs", [])

            if not docs:
                break

            for doc in docs:
                url = doc.get("url")
                portal = doc.get("portal_type")
                if not url or not portal:
                    continue
                results.append(SolrDocument(
                    id=doc.get("id", ""),
                    url=url,
                    portal_type=portal,
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
        fq: list[str] = [f"collected_at:[NOW-{self._window_min}MINUTES TO NOW]"]
        url_fq = self._build_url_contains_fq()
        if url_fq:
            fq.append(url_fq)
        return fq

    def _build_url_contains_fq(self) -> str:
        """
        SOLR_RESCRAPE_URL_CONTAINS 패턴을 Solr fq 표현식으로 변환한다.

        패턴 1개:  url:*naver.com*
        패턴 N개:  (url:*naver.com* OR url:*daum.net*)
        패턴 없음: "" (fq 에 추가하지 않음)
        """
        if not self._url_contains:
            return ""
        clauses = [f"url:*{p}*" for p in self._url_contains]
        return clauses[0] if len(clauses) == 1 else f"({' OR '.join(clauses)})"
