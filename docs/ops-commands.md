# rescrape-dispatcher 운영 명령어 가이드

## 구조 이해

```
rescrape-dispatcher              keyword-crawler
────────────────────             ─────────────────────────────────
Solr 에서 신규 URL 조회           t_article_url 에서 URL 꺼내
  → t_article_url 에               → 본문 페이지 HTTP 요청
    INSERT IGNORE                    → 제목·본문 파싱
    (신규만, 기존 skip)               → Solr 저장
```

rescrape-dispatcher 는 신규 URL 투입만 한다. 추출은 keyword-crawler 의 extraction worker 가 처리한다.

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
# DB 접속 (keyword-crawler 와 같은 RDS)
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

# Solr — keyword-crawler 가 결과를 저장한 코어
SOLR_URL=http://localhost:8983/solr/news
HTTP_VERIFY_SSL=true

# 조회 조건
SOLR_RESCRAPE_QUERY=*:*
SOLR_RESCRAPE_URL_CONTAINS=              # URL contains 패턴 (쉼표 구분, OR 결합)
SLIDING_WINDOW_MINUTES=10                # 슬라이딩 윈도우 크기(분). 주기의 2배 권장
SOLR_RESCRAPE_MAX_DOCS=1000
SOLR_QUERY_BATCH_SIZE=100

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

`SLIDING_WINDOW_MINUTES` 는 주기(`DISPATCH_INTERVAL_SECONDS`)의 2배로 설정한다.

```bash
# 5분 주기 → 윈도우 10분 (기본값)
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

# 실행 (볼륨 마운트 불필요)
docker run --env-file .env.prod rescrape-dispatcher:latest python -m app

# 백그라운드 실행
docker run -d --name rescrape --env-file .env.prod rescrape-dispatcher:latest python -m app

# 로그 확인
docker logs -f rescrape
```

---

## 6. Docker Compose 예시 (keyword-crawler 와 함께)

```yaml
services:
  # keyword-crawler — 발견 워커
  discover-naver-news:
    image: keyword-crawler:latest
    command: ["--role", "discovery", "--source", "naver_news"]
    env_file: keyword-crawler/.env.prod

  # keyword-crawler — 추출 워커
  extraction:
    image: keyword-crawler:latest
    command: ["--role", "extraction"]
    env_file: keyword-crawler/.env.prod
    deploy:
      replicas: 3

  # rescrape-dispatcher
  rescrape-dispatcher:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-1"]
    env_file: rescrape-dispatcher/.env.prod
    restart: unless-stopped
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

  rescrape-daum:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-daum"]
    environment:
      SOLR_RESCRAPE_URL_CONTAINS: "news.daum.net"
    env_file: rescrape-dispatcher/.env.prod
    restart: unless-stopped
```

---

## 7. 상태 확인 (SQL)

```sql
-- rescrape-dispatcher 가 투입한 URL 현황 (priority=5)
SELECT status, COUNT(*) AS cnt
FROM t_article_url
WHERE priority = 5
GROUP BY status
ORDER BY cnt DESC;

-- 오늘 신규 투입된 URL 수
SELECT portal_type, COUNT(*) AS cnt
FROM t_article_url
WHERE priority = 5
  AND created_at >= CURDATE()
GROUP BY portal_type;

-- 투입 후 처리 완료된 URL 수 (오늘)
SELECT portal_type, COUNT(*) AS cnt
FROM t_article_url
WHERE priority = 5
  AND status = 'stored'
  AND updated_at >= CURDATE()
GROUP BY portal_type;
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
| `inserted` | t_article_url 에 실제 삽입된 신규 URL 수 |
| `skipped` | 이미 존재해 INSERT IGNORE 로 skip 된 URL 수 |

`skipped > 0` 은 정상 동작이다 — 슬라이딩 윈도우의 겹침 구간 문서가 skip 된 것.
