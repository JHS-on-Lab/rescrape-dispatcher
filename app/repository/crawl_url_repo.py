"""
crawl_url 테이블 쓰기 접근 — 신규 URL 투입 전용.

keyword-crawler 의 t_crawl_url 테이블에 Solr 에서 조회한 신규 URL 을 삽입한다.
이미 존재하는 URL 은 INSERT IGNORE 로 건드리지 않는다.
본 모듈은 INSERT 만 담당하며, 추출 워커가 이 데이터를 처리한다.

투입 규칙:
  - URL 이 테이블에 없음 → status=discovered 로 INSERT
  - URL 이 이미 존재함  → 변경 없음 (INSERT IGNORE)

MySQL rowcount 해석 (INSERT IGNORE):
  1 → 신규 INSERT
  0 → 중복, skip (또는 다른 SQL 에러 — INSERT IGNORE 는 이 둘을 구분하지 않는다)

rowcount 가 시도한 행 수보다 적으면, 그 차이가 정말 "이미 존재하던 중복"인지
url_hash 로 재조회해서 확인한다. 중복이 아닌데도 반영 안 된 행이 있으면
(컬럼 길이 초과 등으로 INSERT IGNORE 가 조용히 삼킨 진짜 에러) 경고 로그를 남긴다.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse

from sqlalchemy import Engine, bindparam, text

from app.repository.domain_repo import DomainRepo
from app.types import SolrDocument

_log = logging.getLogger(__name__)


# 추적 파라미터 제거 목록 (keyword-crawler url_normalizer 와 동일)
_STRIP_PARAMS = re.compile(
    r"^(utm_source|utm_medium|utm_campaign|utm_term|utm_content"
    r"|fbclid|gclid|msclkid|ref|source)$",
    re.IGNORECASE,
)


def _normalize(url: str) -> str:
    """URL 정규화 — keyword-crawler 와 동일한 로직으로 url_hash 일치를 보장한다."""
    parsed = urlparse(url.strip())
    scheme = "https"
    netloc = parsed.netloc.lower().rstrip(":")
    if netloc.endswith(":443") or netloc.endswith(":80"):
        netloc = netloc.rsplit(":", 1)[0]
    path = parsed.path.rstrip("/") or "/"
    qs = [(k, v) for k, v in parse_qsl(parsed.query) if not _STRIP_PARAMS.match(k)]

    # v.daum.net ?f=o → 언론사 원본으로 리다이렉트됨 → v.daum.net 도메인룰 미적용 방지
    if netloc == "v.daum.net":
        qs = [(k, v) for k, v in qs if k != "f"]

    query = urlencode(sorted(qs))
    return urlunparse((scheme, netloc, path, "", query, ""))


def _url_hash(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode()).hexdigest()


_INSERT_SQL = text("""
    INSERT IGNORE INTO t_crawl_url
        (url, url_hash, host, source_type, status,
         attempt_count, is_manual, priority,
         collected_date, created_at, updated_at)
    VALUES
        (:url, :hash, :host, :source, 'discovered',
         0, false, :priority,
         :cdate, NOW(), NOW())
""")

# INSERT IGNORE 는 url_hash 중복 외의 에러(컬럼 길이 초과 등)도 경고로 바꿔 조용히
# 삼킨다. rowcount 만으로는 "정상 중복 skip"과 "진짜 실패"를 구분할 수 없어서,
# insert 후 실제로 존재하는지 직접 확인한다.
_EXISTS_SQL = text(
    "SELECT url_hash FROM t_crawl_url WHERE url_hash IN :hashes"
).bindparams(bindparam("hashes", expanding=True))


class CrawlUrlRepo:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._domain_repo = DomainRepo(engine)

    def bulk_insert_new(
        self,
        docs: list[SolrDocument],
        priority: int,
    ) -> tuple[int, int]:
        """
        Solr 문서 목록을 t_crawl_url 에 신규 투입한다.
        이미 존재하는 URL 은 INSERT IGNORE 로 skip 된다.
        t_domain.excluded=1 인 host 는 애초에 insert 대상에서 제외한다.

        반환: (total_docs, inserted)
          - total_docs: 처리 시도한 문서 수
          - inserted:   실제 INSERT 된 신규 URL 수 (rowcount 합계)
        """
        if not docs:
            return 0, 0

        now = datetime.now(timezone.utc)
        candidates = []
        for doc in docs:
            norm = _normalize(doc.url)
            candidates.append({
                "url":      norm,
                "hash":     _url_hash(norm),
                "host":     urlparse(norm).netloc,
                "source":   doc.source_type,
                "priority": priority,
                "cdate":    now.date(),
            })

        excluded_hosts = self._domain_repo.get_excluded_hosts(
            list({row["host"] for row in candidates})
        )
        rows = [row for row in candidates if row["host"] not in excluded_hosts]
        if not rows:
            return len(candidates), 0

        with self._engine.begin() as conn:
            result = conn.execute(_INSERT_SQL, rows)
            inserted = result.rowcount

            if inserted < len(rows):
                self._warn_on_silent_failures(conn, rows)

        return len(candidates), inserted

    def _warn_on_silent_failures(self, conn, rows: list[dict]) -> None:
        """
        rowcount < len(rows) 인 경우, 그 차이가 전부 "이미 존재하던 정상 중복"인지
        확인한다. INSERT 시도했는데 지금도 존재하지 않는 행이 있다면 INSERT IGNORE 가
        진짜 에러(중복이 아닌 다른 이유)를 조용히 삼킨 것이므로 별도로 경고 로그를 남긴다.
        """
        hashes = [row["hash"] for row in rows]
        existing = {
            r[0] for r in conn.execute(_EXISTS_SQL, {"hashes": hashes})
        }
        missing = [row for row in rows if row["hash"] not in existing]
        if missing:
            _log.warning(
                f"INSERT IGNORE 후 {len(missing)}건이 중복 skip 이 아니라 실제로 "
                f"누락됨(SQL 에러 등으로 조용히 실패했을 가능성) — url: "
                f"{[m['url'] for m in missing[:10]]}"
                + (" ..." if len(missing) > 10 else "")
            )
