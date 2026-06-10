# rescrape-dispatcher 운영 명령어 가이드

## 구조 이해

```
rescrape-dispatcher              keyword-collector
────────────────────             ─────────────────────────────────
Solr 에서 URL 조회               t_article_url 에서 URL 꺼내
  → t_article_url 에               → 본문 페이지 HTTP 요청
    재투입 (discovered)              → 제목·본문 파싱
                                     → Solr 저장
```

rescrape-dispatcher 는 URL 투입만 한다. 추출은 keyword-collector 의 extraction worker 가 처리한다.

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
# DB 접속 (keyword-collector 와 같은 RDS)
RDS_HOST=
RDS_PORT=3306
RDS_USER=
RDS_PASSWORD=
RDS_DB=keyword_collector

# SSH 터널 (로컬에서 RDS 직접 접근 시)
TUNNEL_ENABLED=true
TUNNEL_SSH_HOST=
TUNNEL_SSH_USER=ubuntu
TUNNEL_SSH_KEY_PATH=
TUNNEL_LOCAL_PORT=13307

# Solr — keyword-collector 가 결과를 저장한 코어
SOLR_URL=http://localhost:8983/solr/news
HTTP_VERIFY_SSL=true

# 재수집 조건
SOLR_RESCRAPE_QUERY=*:*
SOLR_RESCRAPE_FQ=collected_at:[* TO NOW-7DAYS]
SOLR_RESCRAPE_MAX_DOCS=1000
SOLR_QUERY_BATCH_SIZE=100

# 스케줄
DISPATCH_INTERVAL_SECONDS=3600
RESCRAPE_PRIORITY=5
WORKER_ID=rescrape-1

# 로깅
LOG_DIR=./logs
LOG_LEVEL=INFO
LOG_ROTATION=daily
HEARTBEAT_INTERVAL_SECONDS=60
```

---

## 3. 재수집 조건 예시

`SOLR_RESCRAPE_FQ` 환경변수로 재수집 대상을 조정한다.

```bash
# 7일 이상 지난 기사
SOLR_RESCRAPE_FQ=collected_at:[* TO NOW-7DAYS]

# 3일 이상 지난 기사
SOLR_RESCRAPE_FQ=collected_at:[* TO NOW-3DAYS]

# 특정 포털만
SOLR_RESCRAPE_FQ=portal_type:NAVER_NEWS

# 복수 포털
SOLR_RESCRAPE_FQ=portal_type:(NAVER_NEWS DAUM_NEWS)

# 본문이 짧은 기사 (재추출 가치 있는 것)
SOLR_RESCRAPE_FQ=body_len:[0 TO 300]

# 기간 + 포털 조합
SOLR_RESCRAPE_FQ=portal_type:NAVER_NEWS AND collected_at:[* TO NOW-7DAYS]
```

---

## 4. Docker 실행

```bash
# 이미지 빌드
docker build -t rescrape-dispatcher:latest .

# 실행
docker run --env-file .env.prod rescrape-dispatcher:latest python -m app

# 백그라운드 실행
docker run -d --name rescrape --env-file .env.prod rescrape-dispatcher:latest python -m app

# 로그 확인
docker logs -f rescrape
```

---

## 5. Docker Compose 예시 (keyword-collector 와 함께)

```yaml
services:
  # keyword-collector — 발견 워커
  discover-naver-news:
    image: keyword-collector:latest
    command: ["--role", "discovery", "--portal", "naver_news"]
    env_file: keyword-collector/.env.prod

  # keyword-collector — 추출 워커
  extraction:
    image: keyword-collector:latest
    command: ["--role", "extraction"]
    env_file: keyword-collector/.env.prod
    deploy:
      replicas: 3

  # rescrape-dispatcher
  rescrape-dispatcher:
    image: rescrape-dispatcher:latest
    command: ["python", "-m", "app", "--worker-id", "rescrape-1"]
    env_file: rescrape-dispatcher/.env.prod
    restart: unless-stopped
```

---

## 6. 상태 확인 (SQL)

```sql
-- rescrape-dispatcher 가 투입한 URL 현황
-- (is_manual=false, keyword_id=NULL 인 경우가 신규 투입)
SELECT status, COUNT(*) AS cnt
FROM t_article_url
WHERE keyword_id IS NULL
GROUP BY status
ORDER BY cnt DESC;

-- 재수집 대기 중인 URL 수 (priority=5 = rescrape 기본값)
SELECT portal_type, COUNT(*) AS cnt
FROM t_article_url
WHERE status = 'discovered' AND priority = 5
GROUP BY portal_type;

-- 재수집 후 처리 완료된 URL 수 (오늘)
SELECT portal_type, COUNT(*) AS cnt
FROM t_article_url
WHERE status = 'stored'
  AND updated_at >= CURDATE()
GROUP BY portal_type;

-- t_article_url 전체 상태 현황
SELECT status, COUNT(*) AS cnt
FROM t_article_url
GROUP BY status
ORDER BY cnt DESC;
```

---

## 7. 로그 확인

```bash
# 진행 로그 (tail)
tail -f logs/rescrape-rescrape-1.log

# 에러 로그만
tail -f logs/rescrape-rescrape-1-error.log

# 최근 사이클 결과 확인
grep "cycle_done" logs/rescrape-rescrape-1.log | tail -5
```

---

## 8. article_url 상태값 참조

| status | 의미 | rescrape-dispatcher 가 재투입하는가 |
|---|---|---|
| `discovered` | 수집 대기 | 건드리지 않음 (이미 대기 중) |
| `extracting` | 추출 중 | 건드리지 않음 (처리 중) |
| `stored` | 완료 | **discovered 로 리셋** |
| `failed_transient` | 일시 실패, 재시도 예약 | 건드리지 않음 (재시도 예약됨) |
| `failed_permanent` | 영구 실패 (404 등) | **discovered 로 리셋** |
| `dead` | 최대 시도 초과 | **discovered 로 리셋** |
