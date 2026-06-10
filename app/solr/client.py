"""
Solr 조회 클라이언트 — 재수집 대상 URL 목록을 가져온다.

Solr cursor 기반 페이지네이션을 사용해 대량 결과를 안전하게 처리한다.
  - 첫 요청: cursorMark=*
  - 다음 요청: 이전 응답의 nextCursorMark 값 사용
  - 종료 조건: nextCursorMark == 이전 cursorMark (결과 소진)

Solr 커서 기반 페이지네이션 전제 조건:
  - sort 에 고유 필드(id) 포함 필수 → sort=id asc
  - rows(= SOLR_QUERY_BATCH_SIZE)는 고정 크기로 유지

조회 필드:
  id            — url_hash (keyword-collector SolrSink 의 문서 id)
  url           — 원본 URL
  portal_type   — 포털 유형 (NAVER_NEWS, DAUM_NEWS, etc.)
"""

from __future__ import annotations

import logging

import httpx

from app import config
from app.types import SolrDocument

logger = logging.getLogger(__name__)

# Solr 에서 가져올 필드 목록
_FL = "id,url,portal_type"


class SolrClient:
    def __init__(self) -> None:
        self._base_url  = config.SOLR_URL.rstrip("/")
        self._batch     = config.SOLR_QUERY_BATCH_SIZE
        self._max_docs  = config.SOLR_RESCRAPE_MAX_DOCS
        self._query     = config.SOLR_RESCRAPE_QUERY
        self._fq        = config.SOLR_RESCRAPE_FQ
        self._http      = httpx.Client(
            timeout=30.0,
            verify=config.HTTP_VERIFY_SSL,
        )

    def query_rescrape_candidates(self) -> list[SolrDocument]:
        """
        설정된 조건(SOLR_RESCRAPE_QUERY + SOLR_RESCRAPE_FQ)으로 Solr를 조회해
        재수집 대상 URL 목록을 반환한다.
        SOLR_RESCRAPE_MAX_DOCS 를 초과하면 그 시점에서 중단한다.
        """
        results: list[SolrDocument] = []
        cursor = "*"

        while len(results) < self._max_docs:
            batch_size = min(self._batch, self._max_docs - len(results))
            params: dict = {
                "q":          self._query,
                "fl":         _FL,
                "rows":       batch_size,
                "sort":       "id asc",
                "cursorMark": cursor,
                "wt":         "json",
            }
            if self._fq:
                params["fq"] = self._fq

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
                break  # 더 이상 결과 없음
            cursor = next_cursor

        return results

    def close(self) -> None:
        self._http.close()
