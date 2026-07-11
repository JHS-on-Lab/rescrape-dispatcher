# rescrape-dispatcher — 설계 문서

> 이 문서는 구현 에이전트(Claude Code)가 읽고 개발에 착수하기 위한 설계 명세다.
> 명세에서 벗어나야 할 경우 이 문서를 먼저 갱신한다.

---

## 1. 개요

Solr 에 새로 수집된 콘텐츠 중 **특정 URL 패턴을 가진 신규 문서**를 주기적으로 조회해,
`discovery-worker` / `extraction-worker` 가 사용하는 `t_crawl_url` 테이블에 투입하는 서비스다.

- **입력**: Solr DB (extraction-worker 의 결과 저장소)
- **출력**: MySQL `t_crawl_url` 테이블 (discovery-worker / extraction-worker 와 공유)
- **이후 처리**: extraction-worker 가 `t_crawl_url` 에서 URL 을 꺼내 본문을 추출한다.

### 1.1 discovery-worker / extraction-worker 와의 관계

```
[discovery-worker]                    [rescrape-dispatcher]
  Discovery worker                        SolrClient
    → t_crawl_url (discovered)              Solr 신규 문서 조회
                                                ↓
[extraction-worker]                     t_crawl_url INSERT IGNORE
  Extraction worker                       (신규 URL 만 삽입)
    → 본문 추출                                 ↓
    → Solr 저장                         [extraction-worker]
                                          Extraction worker
                                            → 본문 추출 → Solr 업데이트
```

**이 프로젝트는 discovery-worker / extraction-worker 코드를 수정하거나 공유하지 않는다.**
두 프로젝트는 **동일한 MySQL DB** (`t_crawl_url`)를 통해서만 소통한다.

---

## 2. 아키텍처

```
                 ┌─────────────────────────────────┐
                 │        rescrape-dispatcher       │
                 │                                  │
  Solr ────────► │  SolrClient.query_rescrape()     │
                 │    슬라이딩 윈도우 조회           │
                 │          ↓                       │
                 │  CrawlUrlRepo.bulk_insert_new() │
                 │      INSERT IGNORE               │
                 └────────────────┬────────────────┘
                                  │ INSERT (신규만)
                                  ▼
                         t_crawl_url (MySQL)
                                  │
                                  ▼ (extraction-worker 이 읽음)
                         Extraction worker
```

---

## 3. 동작 흐름

```
1. 설정 로드 (환경변수 / .env)
2. DB 연결 (SSH 터널 옵션)
3. 루프 시작:
   a. SolrClient → /select 요청 (슬라이딩 윈도우 + cursor 페이지네이션)
      조건: collected_at:[NOW-{WINDOW}MINUTES TO NOW]
            + SOLR_RESCRAPE_URL_CONTAINS (설정 시)
      최대: SOLR_MAX_DOCS 건
   b. CrawlUrlRepo.bulk_insert_new()
      - 신규 URL → status=discovered 로 INSERT
      - 이미 존재하는 URL → 변경 없음 (INSERT IGNORE)
   c. 결과 로그 기록 (fetched / inserted / skipped)
   d. DISPATCH_INTERVAL_SECONDS 대기
   e. → 3으로 반복
```

---

## 4. 슬라이딩 윈도우 방식

### 4.1 개념

매 사이클마다 **현재 시각 기준 과거 N분 이내** 문서를 조회한다.

```
주기 5분, 윈도우 10분 설정 시:

10:00 사이클: collected_at:[09:50 TO NOW] 조회
10:05 사이클: collected_at:[09:55 TO NOW] 조회
10:10 사이클: collected_at:[10:00 TO NOW] 조회
              ↑___↑ 5분 겹침 구간은 INSERT IGNORE 로 skip
```

### 4.2 권장 윈도우 설정

`SLIDING_WINDOW_MINUTES` = `DISPATCH_INTERVAL_SECONDS / 60 × 2` (주기의 2배)

| 주기 | 권장 윈도우 |
|---|---|
| 5분 (300s) | 10분 |
| 10분 (600s) | 20분 |
| 1시간 (3600s) | 120분 |

윈도우가 주기보다 작으면 주기 사이에 수집된 문서가 누락될 수 있다.

### 4.3 컨테이너 재시작 / 다운타임 시 동작 — 워터마크 (2026-07-11 추가)

**직접 모드(SOLR_DIRECT_ENABLED=true)**: 상태 저장 없음. 재시작 시 항상
"NOW - 윈도우"부터 다시 조회한다. 다운타임이 윈도우보다 길면 그 갭은 영구 누락된다.

**DB 조회 모드**: `trendtracker.t_di_config_v1`은 외부 소유 테이블이라 컬럼을
추가할 권한이 없어, 워터마크(마지막 성공 조회 시각)를 `WATERMARK_DIR` 아래
로컬 파일로 저장한다(`app/repository/watermark_store.py`). 다운타임으로 워터마크가
슬라이딩 윈도우보다 뒤처지면, 그 갭을 `SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES`
(기본 1440분=24시간) 상한까지 확장해서 따라잡는다. 상한을 넘는 다운타임은 그
지점까지만 복구되고 그 이전 구간은 여전히 누락된다.

파일이 없거나(최초 실행), 볼륨 마운트 없이 컨테이너가 완전히 새로 생성되면
워터마크가 초기화되고 순수 슬라이딩 윈도우로 폴백한다 — 데이터 오염 없이
동작이 저하될 뿐이다. 재시작 후에도 워터마크를 유지하려면 `WATERMARK_DIR`을
호스트 볼륨으로 마운트할 것(다른 워커들의 chrome_profile/logs 마운트와 동일 패턴).

### 4.4 멀티 인스턴스

복수 인스턴스가 같은 조건으로 동시에 실행돼도 안전하다.
`url_hash` UNIQUE 제약 + INSERT IGNORE 로 먼저 들어온 것만 삽입되고 나머지는 skip.

워터마크 파일 경로는 `WORKER_ID`로 분리된다(같은 DI 설정을 쓰는 인스턴스라도
서로 다른 파일에 쓴다) — 워터마크 디렉터리를 여러 컨테이너가 공유 마운트해도
동시 읽기/쓰기 경합이 발생하지 않는다.

---

## 5. Solr 조회 전략

### 5.1 커서 기반 페이지네이션

```
GET /select?q={query}&fl=id,url
           &fq=tstamp:[{ts_start} TO {ts_now}]
           &fq={filter_query}               (t_di_config_v1.filter_query 설정 시)
           &rows=100&sort=tstamp+asc,id+asc
           &cursorMark=*&wt=json
→ 응답에서 nextCursorMark 를 꺼내 다음 요청에 사용
→ nextCursorMark == 이전 cursorMark 이면 결과 소진 → 종료
```

**sort 에 `tstamp asc` 필수**: 슬라이딩 윈도우는 시간 기반이므로
`id asc` 단독 정렬은 윈도우 내 문서를 시간 순서대로 처리하지 않는다.
cursorMark 의 안정성을 위해 `id asc` 를 두 번째 정렬 키로 함께 지정한다.

### 5.2 조회 조건 설정

| 환경변수 | Solr 파라미터 | 설명 | 예시 |
|---|---|---|---|
| `SOLR_QUERY` | `q` | 기본 쿼리 | `*:*` (기본) |
| `SLIDING_WINDOW_MINUTES` | `fq` | 슬라이딩 윈도우 크기(분) | `10` |
| `SOLR_RESCRAPE_URL_CONTAINS` | `fq` | URL contains 패턴 필터 | `#keyword` |
| `SOLR_MAX_DOCS` | — | 1사이클 최대 URL 수 | `1000` |
| `SOLR_QUERY_BATCH_SIZE` | — | Solr 요청 1회당 rows | `100` |

(환경변수 이름은 `app/config.py` 실제 정의 기준. `SOLR_RESCRAPE_URL_CONTAINS`만
`RESCRAPE` 접두어가 붙고 나머지는 안 붙는 게 일관성은 없지만, 코드 변경 없이
이 문서를 실제 이름에 맞춰 정정한 것 — 서버 .env 를 바꿀 필요 없음.)

#### SOLR_RESCRAPE_URL_CONTAINS 예시

쉼표로 구분된 문자열 목록. 각 패턴은 `url:*{패턴}*` wildcard 로 변환되며 여러 패턴은 OR 결합.

```bash
# URL 끝에 #keyword 가 붙은 것만
SOLR_RESCRAPE_URL_CONTAINS=#keyword

# 특정 도메인
SOLR_RESCRAPE_URL_CONTAINS=naver.com

# 복수 패턴 (OR)
SOLR_RESCRAPE_URL_CONTAINS=naver.com,daum.net
```

생성되는 Solr fq:
- 패턴 1개: `url:*#keyword*`
- 패턴 N개: `(url:*naver.com* OR url:*daum.net*)`

### 5.3 조회 필드

Solr 에서 가져오는 필드: `id`, `url`

`source_type` 은 Solr 스키마에 없으므로 조회하지 않고
`SOLR_RESCRAPE_SOURCE_TYPE`(`"SOLR_RESCRAPE"`) 상수로 고정해 t_crawl_url 에 삽입한다.

---

## 6. DB 연동

### 6.1 t_crawl_url 투입 규칙

`url_hash` UNIQUE 제약 기준 INSERT IGNORE.

| 기존 상태 | 처리 |
|---|---|
| 없음 (신규 URL) | `status=discovered` 로 INSERT |
| 이미 존재 (어떤 status 든) | 변경 없음 (INSERT IGNORE skip) |

재수집 목적이 아니라 **신규 투입**이 목적이므로 기존 URL 의 상태를 변경하지 않는다.

**알려진 엣지케이스 (2026-07-11)**: 만약 이 요약본 전용 크롤러가 대상으로 삼은 URL이
discovery-worker 에 의해 별도로 이미 발견된 적이 있고, 그때 추출이 실패해서
t_crawl_url 에 `failed_permanent`/`dead` 상태로 이미 존재하는 경우 — 이 경우도
"이미 존재하면 skip"에 걸려서 재수집이 트리거되지 않는다. "요약본 크롤러의 대상
URL과 discovery-worker 의 대상 URL이 겹치면서 + 하필 그 시도가 실패했던 경우"라는
이중 조건이 필요해서 발생 빈도는 낮을 것으로 추정되지만, 발생하면 해당 URL의
전문은 이 경로로는 채워지지 않는다. 확인 필요 시 t_crawl_url 에서 해당 url_hash의
기존 status 를 직접 조회해서 판단할 것.

### 6.2 우선순위

투입 URL 에는 `RESCRAPE_PRIORITY`(기본: 5) 를 부여한다.
discovery-worker 발견자 삽입의 기본 priority(0) 보다 높아
extraction-worker 가 이 URL 을 먼저 처리한다.

### 6.3 url_hash 일치 보장

discovery-worker / extraction-worker 와 동일한 URL 정규화 로직을 `crawl_url_repo.py` 에 복제 적용한다.

정규화 규칙: http→https, 호스트 소문자, 추적 파라미터 제거, 끝 슬래시 제거, 기본 포트 제거, 프래그먼트 제거.

---

## 7. 스케줄링

- 단일 루프 프로세스. cron 이 아니라 내부 `time.sleep(DISPATCH_INTERVAL_SECONDS)` 로 반복.
- 기본 주기: 300초(5분). 환경변수로 조정 가능.
- 복수 인스턴스를 띄워도 INSERT IGNORE 멱등성으로 중복 투입은 안전하다.

---

## 8. 모듈 구조

```
app/
  __main__.py            # 진입점 (argparse, signal, validate, run)
  config.py              # 환경변수 로딩 + validate()
  logging_setup.py       # 로그 파일 / 콘솔 핸들러 설정
  types.py               # SolrDocument, DispatchStats

  repository/
    db.py                # SSH 터널(옵션) + SQLAlchemy 엔진 context manager
    crawl_url_repo.py  # t_crawl_url bulk_insert_new()

  solr/
    client.py            # SolrClient — 슬라이딩 윈도우 + cursor 페이지네이션

  scheduling/
    dispatcher.py        # run_dispatch_loop() — 메인 루프
```

---

## 9. 설정 키 전체 목록

| 키 | 기본값 | 설명 |
|---|---|---|
| `RDS_HOST` | (필수) | MySQL 호스트 |
| `RDS_PORT` | `3306` | MySQL 포트 |
| `RDS_USER` | (필수) | MySQL 사용자 |
| `RDS_PASSWORD` | (필수) | MySQL 비밀번호 |
| `RDS_CRAWLER_DB` | (필수) | INSERT 대상 스키마 (t_crawl_url) |
| `RDS_TRENDTRACKER_DB` | `trendtracker` | SELECT 대상 스키마 (t_di_config_v1) |
| `TUNNEL_ENABLED` | `false` | SSH 터널 사용 여부 |
| `TUNNEL_SSH_HOST` | — | SSH 서버 호스트 |
| `TUNNEL_SSH_PORT` | `22` | SSH 서버 포트 |
| `TUNNEL_SSH_USER` | `ubuntu` | SSH 사용자 |
| `TUNNEL_SSH_KEY_PATH` | — | SSH 키 파일 경로 |
| `TUNNEL_LOCAL_PORT` | `13307` | 로컬 터널 포트 |
| `WORKER_ID` | `rescrape-1` | 워커 식별자 |
| `SOLR_URL` | (필수) | Solr 코어 URL |
| `HTTP_VERIFY_SSL` | `true` | SSL 검증 여부 |
| `SOLR_QUERY` | `*:*` | Solr q 파라미터 |
| `SOLR_RESCRAPE_URL_CONTAINS` | `` | URL contains 패턴 (쉼표 구분, OR 결합) |
| `SLIDING_WINDOW_MINUTES` | `10` | 슬라이딩 윈도우 크기(분). 주기의 2배 권장 |
| `SOLR_QUERY_BATCH_SIZE` | `100` | Solr 요청 1회당 rows |
| `SOLR_MAX_DOCS` | `1000` | 1사이클 최대 URL 수 |
| `WATERMARK_DIR` | `./watermark` | 워터마크 로컬 파일 저장 경로 (DB 조회 모드 전용) |
| `SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES` | `1440` | 다운타임 후 최대 이만큼(분)까지만 과거로 확장 조회 |
| `DISPATCH_INTERVAL_SECONDS` | `300` | 사이클 반복 주기(초). 기본 5분 |
| `RESCRAPE_PRIORITY` | `5` | 투입 URL 우선순위 |
| `LOG_DIR` | `./logs` | 로그 디렉토리 |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |
| `LOG_ROTATION` | `daily` | 로그 로테이션 방식 |
| `HEARTBEAT_INTERVAL_SECONDS` | `60` | 하트비트 주기(초) |

---

## 10. 배포

### 10.1 Docker 이미지

discovery-worker / extraction-worker 와 달리 Playwright / 헤드리스 브라우저가 필요 없으므로
`python:3.12-slim` 경량 이미지를 사용한다.

```bash
docker build -t rescrape-dispatcher:latest .
```

### 10.2 실행 예

```bash
# 기본 실행
docker run --env-file .env.prod rescrape-dispatcher:latest python -m app

# 워커 ID 명시
docker run --env-file .env.prod rescrape-dispatcher:latest python -m app --worker-id rescrape-prod-1
```

볼륨 마운트 불필요 (슬라이딩 윈도우는 상태를 저장하지 않는다).

### 10.3 Docker Compose 예시

```yaml
services:
  rescrape-dispatcher:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-1"]
    env_file: .env.prod
    restart: unless-stopped
```

멀티 인스턴스 (다른 URL 패턴 처리):

```yaml
services:
  rescrape-naver:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-naver"]
    environment:
      SOLR_RESCRAPE_URL_CONTAINS: "news.naver.com"
    env_file: .env.prod
    restart: unless-stopped

  rescrape-daum:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-daum"]
    environment:
      SOLR_RESCRAPE_URL_CONTAINS: "news.daum.net"
    env_file: .env.prod
    restart: unless-stopped
```

---

## 11. 관측성 / 로깅

### 11.1 로그 파일

| 파일 | 내용 |
|---|---|
| `{LOG_DIR}/rescrape-{worker_id}.log` | 정상 동작·진행·하트비트 (INFO 이상) |
| `{LOG_DIR}/rescrape-{worker_id}-error.log` | WARNING 이상만 |

### 11.2 주요 로그 항목

```
# 시작
2026-06-11T09:00:00Z INFO  [main] worker=rescrape-1 phase=startup dispatch loop started
2026-06-11T09:00:00Z INFO  [main] worker=rescrape-1 phase=startup config: query='*:*' window=10min url_contains='#keyword' max_docs=1000 interval=300s

# 1사이클 완료
2026-06-11T09:00:02Z INFO  [dispatcher] worker=rescrape-1 phase=solr_fetch solr fetched=43
2026-06-11T09:00:02Z INFO  [dispatcher] worker=rescrape-1 phase=db_insert db insert total=43 inserted=38 skipped=5
2026-06-11T09:00:02Z INFO  [dispatcher] worker=rescrape-1 phase=cycle_done cycle=1 fetched=43 inserted=38 elapsed=2.1s next_run=09:05 KST

# 하트비트
2026-06-11T09:01:02Z INFO  [dispatcher] worker=rescrape-1 phase=heartbeat heartbeat cycle=1
```

### 11.3 inserted / skipped 해석

| 항목 | 의미 |
|---|---|
| `fetched` | Solr 에서 가져온 문서 수 |
| `inserted` | t_crawl_url 에 실제 삽입된 신규 URL 수 |
| `skipped` | 이미 존재해 INSERT IGNORE 로 skip 된 URL 수 (`fetched - inserted`) |

`skipped > 0` 은 정상 동작이다 — 윈도우 내 중복 조회 구간의 문서가 skip 된 것.

---

## 12. discovery-worker / extraction-worker 와의 차이점

| 항목 | discovery-worker | extraction-worker | rescrape-dispatcher |
|---|---|---|---|
| 역할 | URL 발견 | 본문 추출 | 신규 URL 투입만 |
| 입력 | 포털 검색 결과 (스크래핑) | t_crawl_url | Solr (HTTP JSON API) |
| 출력 | t_crawl_url | t_crawl_url + Solr | t_crawl_url 만 |
| 베이스 이미지 | playwright/python | playwright/python | python:3.12-slim |
| 의존성 | httpx, undetected-chromedriver 등 | Playwright, trafilatura, lxml 등 | SQLAlchemy, httpx 만 |
| 스케줄링 | DB 기반 키워드 스케줄 | DB 기반 URL 큐 | 단순 time.sleep 루프 |
| Docker 볼륨 | 필요 (로그) | 필요 (로그, 출력) | 필요 (로그) |

---

## 13. 범위 밖

- 본문 추출 로직 — extraction-worker 가 처리
- Solr 스키마 변경 — extraction-worker 프로젝트에서 관리
- t_crawl_url 스키마 변경 — discovery-worker alembic 마이그레이션으로 관리
- 기존 URL 재추출 (stored → discovered 리셋) — 이 프로젝트의 범위 밖
