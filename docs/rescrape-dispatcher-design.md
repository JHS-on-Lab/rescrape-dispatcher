# rescrape-dispatcher — 설계 문서

> 이 문서는 구현 에이전트(Claude Code)가 읽고 개발에 착수하기 위한 설계 명세다.
> 명세에서 벗어나야 할 경우 이 문서를 먼저 갱신한다.

---

## 1. 개요

`keyword-collector` 가 Solr 에 저장해 둔 기사 중 **특정 조건을 만족하는 URL**을 주기적으로 조회해, `keyword-collector` 가 사용하는 `t_article_url` 테이블에 재수집 대상으로 재투입(dispatch)하는 서비스다.

- **입력**: Solr DB (keyword-collector 의 결과 저장소)
- **출력**: MySQL `t_article_url` 테이블 (keyword-collector 와 공유)
- **이후 처리**: keyword-collector 의 extraction worker 가 `t_article_url` 에서 URL 을 꺼내 본문을 다시 추출한다.

### 1.1 keyword-collector 와의 관계

```
[keyword-collector]                     [rescrape-dispatcher]
  Discovery worker                        SolrClient
    → t_article_url (discovered)              Solr 조회
  Extraction worker                           ↓
    → 본문 추출                           t_article_url 재투입
    → Solr 저장                         (discovered / 상태 리셋)
                                              ↓
                                        [keyword-collector]
                                          Extraction worker
                                            → 재추출 → Solr 업데이트
```

**이 프로젝트는 keyword-collector 코드를 수정하거나 공유하지 않는다.**
두 프로젝트는 **동일한 MySQL DB** (`t_article_url`)를 통해서만 소통한다.

---

## 2. 아키텍처

```
                 ┌─────────────────────────────────┐
                 │        rescrape-dispatcher       │
                 │                                  │
  Solr ────────► │  SolrClient.query_rescrape()     │
                 │      cursor 기반 페이지네이션     │
                 │          ↓                       │
                 │  ArticleUrlRepo.bulk_upsert()    │
                 │      ON DUPLICATE KEY UPDATE     │
                 └────────────────┬────────────────┘
                                  │ INSERT / UPDATE
                                  ▼
                         t_article_url (MySQL)
                                  │
                                  ▼ (keyword-collector 이 읽음)
                         Extraction worker
```

---

## 3. 동작 흐름

```
1. 설정 로드 (환경변수 / .env)
2. DB 연결 (SSH 터널 옵션)
3. 루프 시작:
   a. SolrClient → /select 요청 (cursor 기반 페이지네이션)
      조건: SOLR_RESCRAPE_QUERY + SOLR_RESCRAPE_FQ
      최대: SOLR_RESCRAPE_MAX_DOCS 건
   b. ArticleUrlRepo.bulk_upsert_rescrape()
      - 신규 URL                      → status=discovered 로 INSERT
      - stored / failed_permanent / dead → status=discovered 로 UPDATE
      - discovered / extracting / failed_transient → 변경 없음 (skip)
   c. 결과 로그 기록
   d. DISPATCH_INTERVAL_SECONDS 대기
   e. → 3으로 반복
```

---

## 4. Solr 조회 전략

### 4.1 커서 기반 페이지네이션

대량 결과를 안전하게 처리하기 위해 Solr 의 **cursorMark** 를 사용한다.

```
GET /select?q={query}&fl=id,url,portal_type&rows=100&sort=id+asc&cursorMark=*&wt=json
→ 응답에서 nextCursorMark 를 꺼내 다음 요청에 사용
→ nextCursorMark == 이전 cursorMark 이면 결과 소진 → 종료
```

전제 조건: `sort` 에 고유 필드(`id`) 포함 필수.

### 4.2 조회 조건 설정

| 환경변수 | 설명 | 예시 |
|---|---|---|
| `SOLR_RESCRAPE_QUERY` | Solr q 파라미터 | `*:*` (기본) |
| `SOLR_RESCRAPE_FQ` | Solr fq 파라미터 | `collected_at:[* TO NOW-7DAYS]` |
| `SOLR_RESCRAPE_MAX_DOCS` | 1사이클 최대 URL 수 | `1000` |
| `SOLR_QUERY_BATCH_SIZE` | Solr 요청 1회당 rows | `100` |

조건 예시:

```bash
# 7일 이상 지난 기사 재수집
SOLR_RESCRAPE_FQ=collected_at:[* TO NOW-7DAYS]

# 특정 포털의 기사만
SOLR_RESCRAPE_FQ=portal_type:NAVER_NEWS

# 본문이 짧은 기사 재수집
SOLR_RESCRAPE_FQ=body_len:[0 TO 500]

# 조합 (괄호로 묶어 OR/AND)
SOLR_RESCRAPE_FQ=portal_type:(NAVER_NEWS DAUM_NEWS) AND body_len:[0 TO 500]
```

### 4.3 조회 필드

Solr 에서 가져오는 필드: `id`, `url`, `portal_type`

- `id` = `url_hash` (keyword-collector SolrSink 이 저장한 문서 id)
- `url` = 기사 URL
- `portal_type` = 포털 유형 (t_article_url 에 그대로 저장)

---

## 5. DB 연동

### 5.1 t_article_url 재투입 규칙

`url_hash` UNIQUE 제약을 기준으로 ON DUPLICATE KEY UPDATE 를 사용한다.

| 기존 status | 처리 |
|---|---|
| 없음 (신규 URL) | `status=discovered` 로 INSERT |
| `stored` | `status=discovered` 로 UPDATE (재추출) |
| `failed_permanent` | `status=discovered` 로 UPDATE (재시도) |
| `dead` | `status=discovered` 로 UPDATE (재시도) |
| `discovered` | 변경 없음 (이미 대기 중) |
| `extracting` | 변경 없음 (처리 중) |
| `failed_transient` | 변경 없음 (재시도 예약됨) |

재투입 시 함께 초기화하는 컬럼:
- `attempt_count = 0`
- `next_retry_at = NULL`

### 5.2 우선순위

재수집 URL 에는 `RESCRAPE_PRIORITY`(기본: 5) 를 부여한다.
keyword-collector 발견자 삽입의 기본 priority(0) 보다 높아
extraction worker 가 재수집 URL 을 먼저 처리한다.

### 5.3 url_hash 일치 보장

keyword-collector 와 동일한 URL 정규화 로직을 `article_url_repo.py` 에 복제 적용한다.
(두 프로젝트 코드를 공유하지 않기 때문에 복제가 불가피하다.)

정규화 규칙: http→https, 호스트 소문자, 추적 파라미터 제거, 끝 슬래시 제거, 기본 포트 제거, 프래그먼트 제거.

---

## 6. 스케줄링

- 단일 루프 프로세스. cron 이 아니라 내부 `time.sleep(DISPATCH_INTERVAL_SECONDS)` 로 반복.
- 기본 주기: 3600초(1시간). 환경변수로 조정 가능.
- 복수 인스턴스를 띄워도 `ON DUPLICATE KEY UPDATE` 의 멱등성으로 중복 투입은 안전하다.
  (status 가 이미 `discovered` 면 변경 없음.)

---

## 7. 모듈 구조

```
app/
  __main__.py            # 진입점 (argparse, signal, validate, run)
  config.py              # 환경변수 로딩 + validate()
  logging_setup.py       # 로그 파일 / 콘솔 핸들러 설정
  types.py               # SolrDocument, DispatchStats

  repository/
    db.py                # SSH 터널(옵션) + SQLAlchemy 엔진 context manager
    article_url_repo.py  # t_article_url bulk_upsert_rescrape()

  solr/
    client.py            # SolrClient — cursor 기반 페이지네이션 조회

  scheduling/
    dispatcher.py        # run_dispatch_loop() — 메인 루프
```

---

## 8. 설정 키 전체 목록

| 키 | 기본값 | 설명 |
|---|---|---|
| `RDS_HOST` | (필수) | MySQL 호스트 |
| `RDS_PORT` | `3306` | MySQL 포트 |
| `RDS_USER` | (필수) | MySQL 사용자 |
| `RDS_PASSWORD` | (필수) | MySQL 비밀번호 |
| `RDS_DB` | (필수) | MySQL 데이터베이스명 |
| `TUNNEL_ENABLED` | `false` | SSH 터널 사용 여부 |
| `TUNNEL_SSH_HOST` | — | SSH 서버 호스트 |
| `TUNNEL_SSH_PORT` | `22` | SSH 서버 포트 |
| `TUNNEL_SSH_USER` | `ubuntu` | SSH 사용자 |
| `TUNNEL_SSH_KEY_PATH` | — | SSH 키 파일 경로 |
| `TUNNEL_LOCAL_PORT` | `13307` | 로컬 터널 포트 |
| `WORKER_ID` | `rescrape-1` | 워커 식별자 |
| `SOLR_URL` | (필수) | Solr 코어 URL |
| `HTTP_VERIFY_SSL` | `true` | SSL 검증 여부 |
| `SOLR_RESCRAPE_QUERY` | `*:*` | Solr q 파라미터 |
| `SOLR_RESCRAPE_FQ` | `` | Solr fq 파라미터 |
| `SOLR_QUERY_BATCH_SIZE` | `100` | Solr 요청 1회당 rows |
| `SOLR_RESCRAPE_MAX_DOCS` | `1000` | 1사이클 최대 URL 수 |
| `DISPATCH_INTERVAL_SECONDS` | `3600` | 사이클 반복 주기 (초) |
| `RESCRAPE_PRIORITY` | `5` | 재수집 URL 우선순위 |
| `LOG_DIR` | `./logs` | 로그 디렉토리 |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |
| `LOG_ROTATION` | `daily` | 로그 로테이션 방식 |
| `HEARTBEAT_INTERVAL_SECONDS` | `60` | 하트비트 주기 (초) |

---

## 9. 배포

### 9.1 Docker 이미지

keyword-collector 와 달리 Playwright / 헤드리스 브라우저가 필요 없으므로
`python:3.12-slim` 경량 이미지를 사용한다. 이미지 크기가 크게 줄어든다.

```bash
docker build -t rescrape-dispatcher:latest .
```

### 9.2 실행 예

```bash
# 기본 실행
docker run --env-file .env.prod rescrape-dispatcher:latest python -m app

# 워커 ID 명시
docker run --env-file .env.prod rescrape-dispatcher:latest python -m app --worker-id rescrape-prod-1
```

### 9.3 Docker Compose 예시

```yaml
services:
  rescrape-dispatcher:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-1"]
    env_file: .env.prod
    restart: unless-stopped
```

같은 도커 환경에서 keyword-collector 와 함께 배포할 때:

```yaml
services:
  # keyword-collector 서비스들
  discover-naver-news:
    image: keyword-collector:latest
    command: ["--role", "discovery", "--portal", "naver_news"]
    env_file: keyword-collector/.env.prod

  extraction:
    image: keyword-collector:latest
    command: ["--role", "extraction"]
    env_file: keyword-collector/.env.prod
    deploy:
      replicas: 3

  # rescrape-dispatcher 서비스
  rescrape-dispatcher:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app"]
    env_file: rescrape-dispatcher/.env.prod
    restart: unless-stopped
```

---

## 10. 관측성 / 로깅

### 10.1 로그 파일

| 파일 | 내용 |
|---|---|
| `{LOG_DIR}/rescrape-{worker_id}.log` | 정상 동작·진행·하트비트 (INFO 이상) |
| `{LOG_DIR}/rescrape-{worker_id}-error.log` | WARNING 이상만 |

### 10.2 주요 로그 항목

```
# 시작
2026-06-10T09:00:00Z INFO  [main] worker=rescrape-1 phase=startup dispatch loop started
2026-06-10T09:00:00Z INFO  [main] worker=rescrape-1 phase=startup config: query='*:*' fq='collected_at:[* TO NOW-7DAYS]' max_docs=1000 interval=3600s

# 1사이클 완료
2026-06-10T09:00:03Z INFO  [dispatcher] worker=rescrape-1 phase=solr_fetch solr fetched=843
2026-06-10T09:00:05Z INFO  [dispatcher] worker=rescrape-1 phase=db_upsert db upsert total=843 affected_rows=1247
2026-06-10T09:00:05Z INFO  [dispatcher] worker=rescrape-1 phase=cycle_done cycle=1 fetched=843 affected_rows=1247 elapsed=5.2s next_run=10:00 KST

# 하트비트
2026-06-10T09:01:05Z INFO  [dispatcher] worker=rescrape-1 phase=heartbeat heartbeat cycle=1
```

### 10.3 affected_rows 해석

`affected_rows`는 MySQL ON DUPLICATE KEY UPDATE 의 rowcount 합산이다:

| 상황 | 기여값 |
|---|---|
| 신규 INSERT | +1 |
| status 변경 UPDATE | +2 |
| 변경 없음 (skip) | +0 |

따라서 `affected_rows > total_fetched` 일 수 있다.

---

## 11. keyword-collector 와의 차이점

| 항목 | keyword-collector | rescrape-dispatcher |
|---|---|---|
| 역할 | URL 발견 + 본문 추출 | URL 재투입만 |
| 입력 | 포털 검색 결과 (스크래핑) | Solr (HTTP JSON API) |
| 출력 | t_article_url + Solr | t_article_url 만 |
| 베이스 이미지 | playwright/python | python:3.12-slim |
| 의존성 | Playwright, trafilatura, lxml 등 | SQLAlchemy, httpx 만 |
| 역할 인자 | `--role discovery\|extraction` | 없음 (단일 역할) |
| 스케줄링 | DB 기반 키워드 스케줄 | 단순 time.sleep 루프 |

---

## 12. 범위 밖

- 본문 추출 로직 — keyword-collector 가 처리
- Solr 스키마 변경 — keyword-collector 프로젝트에서 관리
- t_article_url 스키마 변경 — keyword-collector alembic 마이그레이션으로 관리
- 추출 결과 모니터링 — keyword-collector 의 collection_log / article_url.status 로 확인
