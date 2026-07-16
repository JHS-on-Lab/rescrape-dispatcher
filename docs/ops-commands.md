# rescrape-dispatcher 운영 명령어 가이드

## 구조 이해

```
rescrape-dispatcher              extraction-worker
────────────────────             ─────────────────────────────────
Solr 에서 신규 URL 조회           t_crawl_url 에서 URL 꺼내
  → t_crawl_url 에               → 본문 페이지 HTTP 요청
    INSERT IGNORE                    → 제목·본문 파싱
    (신규만, 기존 skip)               → Solr 저장
```

rescrape-dispatcher 는 신규 URL 투입만 한다. 추출은 extraction-worker 가 처리한다.

---

## 1. 워커 실행

```bash
# 기본 실행 (WORKER_ID 는 환경변수에서)
python -m app

# 워커 ID 명시
python -m app --worker-id rescrape-1

# 도움말
python -m app --help
```

---

## 2. 환경변수 (.env)

```dotenv
# DB 접속 (discovery-worker / extraction-worker 와 같은 RDS)
RDS_HOST=
RDS_PORT=3306
RDS_USER=
RDS_PASSWORD=
RDS_CRAWLER_DB=crawlerdb

# SSH 터널 (로컬에서 RDS 직접 접근 시)
TUNNEL_ENABLED=true
TUNNEL_SSH_HOST=
TUNNEL_SSH_USER=ubuntu
TUNNEL_SSH_KEY_PATH=
TUNNEL_LOCAL_PORT=13307

# Solr — extraction-worker 가 결과를 저장한 코어
# direct 모드(SOLR_DIRECT_ENABLED=true)에서만 사용. config.validate()가 검증하지
# 않으므로 비워두면 기동은 성공하고 매 사이클 Solr 요청만 조용히 실패한다.
SOLR_DIRECT_ENABLED=false
SOLR_URL=http://localhost:8983/solr/news
HTTP_VERIFY_SSL=true

# DB-lookup 모드(SOLR_DIRECT_ENABLED=false, 기본값)일 때 필수 — trendtracker.t_di_config_v1 조회 키
DI_TNT_ID=
DI_PROJECT_ID=
DI_SERVER_IP=

# 조회 조건 (실제 env var 이름은 app/config.py 기준 — RESCRAPE 접두어 없음)
SOLR_QUERY=*:*
SOLR_RESCRAPE_URL_CONTAINS=              # URL contains 패턴 (쉼표 구분, OR 결합)
SLIDING_WINDOW_MINUTES=30                # 슬라이딩 윈도우 크기(분, 기본값). 주기의 2배 권장
SOLR_MAX_DOCS=1000
SOLR_QUERY_BATCH_SIZE=100

# 워터마크 (DB 조회 모드 전용 — 직접 모드는 순수 슬라이딩 윈도우만 사용)
WATERMARK_DIR=./watermark                              # 로컬 파일 저장 경로. 재시작 후에도 유지하려면 볼륨 마운트
SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES=1440       # 다운타임 후 최대 이만큼(분)까지만 과거로 확장 조회

# 스케줄
DISPATCH_INTERVAL_SECONDS=300            # 기본 5분
RESCRAPE_PRIORITY=5
WORKER_ID=rescrape-1

# 로깅
LOG_DIR=./logs
LOG_LEVEL=INFO
LOG_ROTATION=daily
HEARTBEAT_INTERVAL_SECONDS=60
```

---

## 3. 슬라이딩 윈도우 설정 예시

`SLIDING_WINDOW_MINUTES` 는 주기(`DISPATCH_INTERVAL_SECONDS`)의 2배 이상으로 설정하는
것을 권장한다. 실제 코드 기본값은 `DISPATCH_INTERVAL_SECONDS=300`(5분) +
`SLIDING_WINDOW_MINUTES=30`으로, 권장 배율(2배=10분)보다 더 여유있게(6배) 잡혀 있다 —
아래는 "2배" 규칙을 적용했을 때의 예시이며 실제 기본값과는 다르다.

```bash
# 5분 주기 → 윈도우 10분 (2배 권장 예시. 코드 기본값은 30분)
DISPATCH_INTERVAL_SECONDS=300
SLIDING_WINDOW_MINUTES=10

# 10분 주기 → 윈도우 20분
DISPATCH_INTERVAL_SECONDS=600
SLIDING_WINDOW_MINUTES=20

# 1시간 주기 → 윈도우 120분
DISPATCH_INTERVAL_SECONDS=3600
SLIDING_WINDOW_MINUTES=120
```

---

## 4. URL 패턴 설정 예시

### SOLR_RESCRAPE_URL_CONTAINS

쉼표로 구분된 패턴 목록. 각 패턴은 `url:*{패턴}*` wildcard 쿼리가 되고 여러 패턴은 **OR** 결합된다.

```bash
# URL 끝에 #keyword 가 붙은 것만
SOLR_RESCRAPE_URL_CONTAINS=#keyword

# 단일 도메인
SOLR_RESCRAPE_URL_CONTAINS=naver.com

# 복수 도메인 (OR)
SOLR_RESCRAPE_URL_CONTAINS=naver.com,daum.net

# 경로 패턴
SOLR_RESCRAPE_URL_CONTAINS=/mnews/article/
```

---

## 5. Docker 실행

```bash
# 이미지 빌드
docker build -t rescrape-dispatcher:latest .

# 실행 (직접 모드는 볼륨 불필요. DB 조회 모드는 워터마크 유지하려면 -v 필요)
docker run --env-file .env.prod rescrape-dispatcher:latest python -m app

# 백그라운드 실행 + 워터마크 볼륨 마운트 (DB 조회 모드, 재시작 후에도 이어서 조회)
docker run -d --name rescrape --env-file .env.prod \
    -e WATERMARK_DIR=/app/watermark \
    -v ~/apps/data/rescrape-dispatcher/watermark:/app/watermark \
    rescrape-dispatcher:latest python -m app

# 로그 확인
docker logs -f rescrape
```

`./deploy/run.sh <worker_id>`를 쓰면 이 마운트를 자동으로 처리한다.

---

## 6. Docker Compose 예시 (discovery-worker / extraction-worker 와 함께)

```yaml
services:
  # discovery-worker — 발견 워커
  discover-naver-news:
    image: discovery-worker:latest
    command: ["--source", "naver_news"]
    env_file: discovery-worker/.env.prod

  # extraction-worker — 추출 워커
  extraction:
    image: extraction-worker:latest
    command: ["python", "-m", "app"]
    env_file: extraction-worker/.env.prod
    deploy:
      replicas: 3

  # rescrape-dispatcher
  rescrape-dispatcher:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-1"]
    env_file: rescrape-dispatcher/.env.prod
    restart: unless-stopped
    volumes:
      - ./rescrape-dispatcher/logs:/app/logs
      - ./rescrape-dispatcher/watermark:/app/watermark   # DB-lookup 모드일 때만 필요
```

멀티 인스턴스 (다른 URL 패턴):

```yaml
services:
  rescrape-naver:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-naver"]
    environment:
      SOLR_RESCRAPE_URL_CONTAINS: "news.naver.com"
    env_file: rescrape-dispatcher/.env.prod
    restart: unless-stopped
    volumes:
      - ./rescrape-dispatcher/logs:/app/logs
      - ./rescrape-dispatcher/watermark:/app/watermark

  rescrape-daum:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-daum"]
    environment:
      SOLR_RESCRAPE_URL_CONTAINS: "news.daum.net"
    env_file: rescrape-dispatcher/.env.prod
    restart: unless-stopped
    volumes:
      - ./rescrape-dispatcher/logs:/app/logs
      - ./rescrape-dispatcher/watermark:/app/watermark
```

DB 조회 모드에서 재시작 후에도 워터마크를 유지하려면 서비스마다
`-v ./watermark:/app/watermark` 볼륨을 추가한다. `--worker-id`가 서로 다르므로
같은 디렉터리를 공유 마운트해도 파일이 겹치지 않는다.

---

## 7. 상태 확인 (SQL)

```sql
-- rescrape-dispatcher 가 투입한 URL 현황 (priority=5)
SELECT status, COUNT(*) AS cnt
FROM t_crawl_url
WHERE priority = 5
GROUP BY status
ORDER BY cnt DESC;

-- 오늘 신규 투입된 URL 수
SELECT source_type, COUNT(*) AS cnt
FROM t_crawl_url
WHERE priority = 5
  AND created_at >= CURDATE()
GROUP BY source_type;

-- 투입 후 처리 완료된 URL 수 (오늘)
SELECT source_type, COUNT(*) AS cnt
FROM t_crawl_url
WHERE priority = 5
  AND status = 'stored'
  AND updated_at >= CURDATE()
GROUP BY source_type;
```

---

## 8. 로그 확인

```bash
# 진행 로그 (tail)
tail -f logs/rescrape-rescrape-1.log

# 에러 로그만
tail -f logs/rescrape-rescrape-1-error.log

# 최근 사이클 결과 확인
grep "cycle_done" logs/rescrape-rescrape-1.log | tail -5
```

---

## 9. inserted / skipped 해석

| 항목 | 의미 |
|---|---|
| `fetched` | Solr 에서 가져온 문서 수 |
| `inserted` | t_crawl_url 에 실제 삽입된 신규 URL 수 |
| `skipped` | `fetched - inserted`. 이미 존재해 INSERT IGNORE 로 skip 된 URL과, host가 `t_domain.excluded=1`이라 INSERT를 시도조차 안 한 URL이 합산돼 있다(로그만으로는 구분 불가). |

`skipped > 0` 은 보통 정상 동작이다 — 슬라이딩 윈도우의 겹침 구간 문서가 skip 된 것.
비정상적으로 크면 `t_domain.excluded`에 걸린 host가 많은지 확인할 것.

---

## 10. 진단 스크립트

DB에 쓰지 않는(`run_once.py` 제외) 읽기 전용 확인용 스크립트들.

| 스크립트 | 용도 |
|---|---|
| `scripts/check_db.py` | crawlerdb(`t_crawl_url`)·trendtracker(`t_di_config_v1`) 두 스키마 접속 확인 |
| `scripts/check_di_config.py` | `DI_TNT_ID`/`DI_PROJECT_ID`/`DI_SERVER_IP` 로 `t_di_config_v1` 조회 결과(Solr 접속 정보 포함) 출력 |
| `scripts/check_solr.py` | Solr 접속 모드(직접/DB조회) 판별 → ping → 저장 문서 수 확인 |
| `scripts/check_solr_count.py [--no-window]` | Solr 조회 건수 확인. 기본은 슬라이딩 윈도우 적용, `--no-window` 는 전체 조회 |
| `scripts/check_dispatch.py [--limit N]` | 디스패치 dry-run — Solr 조회까지만 하고 DB에는 INSERT 안 함(기본 20건 출력) |
| `scripts/run_once.py` | 디스패치 1회 실행(Solr 조회 → `t_crawl_url` INSERT) 후 종료. 워커 전체 루프 없이 단건 테스트용 — **DB에 실제로 씀** |

모두 `python scripts/<파일명>` 으로 실행하고, 접속 정보는 워커와 동일하게 `.env`/`.env.{APP_ENV}` 를 읽는다.

> `check_dispatch.py`/`run_once.py`는 `query_rescrape_candidates()`가 watermark 기능
> 추가로 3-tuple을 반환하도록 바뀐 뒤 언패킹이 갱신되지 않아 Solr 조회 단계에서 항상
> 예외로 실패하던 버그가 있었다(`too many values to unpack`) — 이번에 수정함.
