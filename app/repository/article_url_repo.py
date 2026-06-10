"""
article_url 테이블 쓰기 접근 — 재수집 전용.

keyword-collector 의 t_article_url 테이블에 URL 을 재투입한다.
본 모듈은 INSERT / UPDATE 만 담당하며, 추출 워커가 이 데이터를 처리한다.

재투입 규칙:
  - URL 이 테이블에 없음        → 신규 INSERT (status=discovered)
  - 기존 status=stored          → UPDATE to discovered (재추출)
  - 기존 status=failed_permanent → UPDATE to discovered (재시도)
  - 기존 status=dead            → UPDATE to discovered (재시도)
  - 기존 status=discovered      → 변경 없음 (이미 대기 중)
  - 기존 status=extracting      → 변경 없음 (처리 중)
  - 기존 status=failed_transient → 변경 없음 (재시도 예약됨)

MySQL rowcount 해석 (ON DUPLICATE KEY UPDATE):
  1  → 신규 INSERT
  2  → 기존 행 UPDATE (status 변경됨)
  0  → 중복이지만 값 변경 없음 (already discovered/extracting)
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse

from sqlalchemy import Engine, text

from app.types import SolrDocument


# 추적 파라미터 제거 목록 (keyword-collector url_normalizer 와 동일)
_STRIP_PARAMS = re.compile(
    r"^(utm_source|utm_medium|utm_campaign|utm_term|utm_content"
    r"|fbclid|gclid|msclkid|ref|source)$",
    re.IGNORECASE,
)


def _normalize(url: str) -> str:
    """URL 정규화 — keyword-collector 와 동일한 로직으로 url_hash 일치를 보장한다."""
    parsed = urlparse(url.strip())
    scheme = "https"
    netloc = parsed.netloc.lower().rstrip(":")
    if netloc.endswith(":443") or netloc.endswith(":80"):
        netloc = netloc.rsplit(":", 1)[0]
    path = parsed.path.rstrip("/") or "/"
    qs = [(k, v) for k, v in parse_qsl(parsed.query) if not _STRIP_PARAMS.match(k)]
    query = urlencode(sorted(qs))
    return urlunparse((scheme, netloc, path, "", query, ""))


def _url_hash(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode()).hexdigest()


# stored / failed_permanent / dead 인 행만 discovered 로 리셋한다.
# discovered / extracting / failed_transient 는 건드리지 않는다.
_UPSERT_SQL = text("""
    INSERT INTO t_article_url
        (url, url_hash, host, portal_type, status,
         attempt_count, is_manual, priority,
         collected_date, created_at, updated_at)
    VALUES
        (:url, :hash, :host, :portal, 'discovered',
         0, false, :priority,
         :cdate, NOW(), NOW())
    ON DUPLICATE KEY UPDATE
        status        = IF(status IN ('stored', 'failed_permanent', 'dead'), 'discovered', status),
        attempt_count = IF(status IN ('stored', 'failed_permanent', 'dead'), 0, attempt_count),
        next_retry_at = IF(status IN ('stored', 'failed_permanent', 'dead'), NULL, next_retry_at),
        updated_at    = IF(status IN ('stored', 'failed_permanent', 'dead'), NOW(), updated_at)
""")


class ArticleUrlRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def bulk_upsert_rescrape(
        self,
        docs: list[SolrDocument],
        priority: int,
    ) -> tuple[int, int]:
        """
        Solr 문서 목록을 t_article_url 에 재투입한다.

        반환: (total_docs, rows_affected)
          - total_docs:   처리 시도한 문서 수
          - rows_affected: DB 영향 행 수 합계
              (신규 INSERT=1, status 변경 UPDATE=2, 변경 없음=0 의 합)
        """
        if not docs:
            return 0, 0

        now = datetime.now(timezone.utc)
        rows = []
        for doc in docs:
            norm = _normalize(doc.url)
            rows.append({
                "url":      norm,
                "hash":     _url_hash(norm),
                "host":     urlparse(norm).netloc,
                "portal":   doc.portal_type,
                "priority": priority,
                "cdate":    now.date(),
            })

        with self._engine.begin() as conn:
            result = conn.execute(_UPSERT_SQL, rows)

        return len(rows), result.rowcount
