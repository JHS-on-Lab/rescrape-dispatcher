# rescrape-dispatcher

3단계 크롤러 파이프라인의 마지막 단계로, Solr(`extraction-worker`가 저장하는 곳)를
폴링해 이미 수집된 문서 중 특정 URL 패턴에 매칭되는 것을 찾아 `t_crawl_url`에
`status=discovered`, 높은 우선순위로 재삽입한다 — 요약형 페이지처럼 주기적으로
재수집이 필요한 콘텐츠를 다시 처리시키기 위함이다.

```
discovery-worker → t_crawl_url → extraction-worker → Solr
                                                          │
                       rescrape-dispatcher: Solr 폴링 ────┘
                                │
                                ▼
                  t_crawl_url (status=discovered, 재삽입)
```

- Solr 쿼리(슬라이딩 타임윈도우 + 커서 페이지네이션) → `CrawlUrlRepo.bulk_insert_new()`
  → `t_crawl_url`. `url_hash` 유니크 기준 `INSERT IGNORE`로 멱등성 보장, 여러 인스턴스
  동시 실행 가능.
- Solr 접근 모드 (`SOLR_DIRECT_ENABLED`):
  - **direct** (`true`): env의 `SOLR_URL` 등을 직접 사용, 재시작 간 상태 없음.
  - **DB lookup** (`false`, 기본): `trendtracker.t_di_config_v1`(`DI_TNT_ID`/`DI_PROJECT_ID`/`DI_SERVER_IP`
    기준)에서 Solr 접속 정보를 조회하고, 재시작/다운타임에도 이어서 처리하도록 워커별
    "watermark" 파일(마지막 성공 동기화 시각)을 로컬에 유지한다
    (`SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES`만큼 소급).
- **crawlerdb(`t_crawl_url`)와 trendtracker(`t_di_config_v1`)는 서로 다른 DB 서버에 있을 수
  있다** — 두 스키마를 위한 SQLAlchemy 엔진이 분리돼 있다(`app/repository/db.py`의
  `db_context()`/`trendtracker_db_context()`). trendtracker 접속 정보(`RDS_TRENDTRACKER_*`)를
  지정하지 않으면 crawlerdb와 같은 서버라고 가정하고 `RDS_*` 값을 그대로 쓴다. SSH 터널을
  쓰는 경우 bastion(`TUNNEL_SSH_HOST`/`USER`/`KEY_PATH`)은 공유하되, 접속 대상과 로컬
  포워딩 포트(`TUNNEL_LOCAL_PORT` vs `TUNNEL_TRENDTRACKER_LOCAL_PORT`)만 분리된다.
- Solr 스키마나 추출 로직에는 관여하지 않으며, `t_crawl_url`에 대해서만 쓰기 전용이다.
- `t_crawl_url`/`t_di_config_v1` 등 어떤 테이블 스키마도 이 저장소가 마이그레이션 도구로
  관리하지 않는다(`t_crawl_url`은 discovery-worker 소관 `crawlerdb-migrations` 저장소가
  관리; `t_di_config_v1`은 이미 존재하는 테이블을 코드에서 조회만 한다).
- INSERT 전에 `t_domain.excluded=1`인 host는 걸러낸다(discovery-worker와 동일한 도메인
  차단 정책 적용). 로그의 `db_insert` 단계 `skipped` 수치는 "이미 존재하는 중복 URL"과
  "도메인 차단으로 제외된 URL"을 구분하지 않고 합산한 값이다.

자세한 설계는 [docs/rescrape-dispatcher-design.md](docs/rescrape-dispatcher-design.md),
운영 커맨드는 [docs/ops-commands.md](docs/ops-commands.md) 참고.

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 실행 방법

### 로컬

```bash
python -m app                          # WORKER_ID는 env 기본값(rescrape-1) 사용
python -m app --worker-id rescrape-1   # 워커 id 지정 (로그 파일명/watermark 파일에 반영)
python -m app --help
```

`SIGTERM`/`SIGINT` 수신 시 로그를 남기고 정상 종료한다.

### CLI 인자

| 인자 | 설명 | 값 범위 | 기본값 |
|---|---|---|---|
| `--worker-id` | 워커 인스턴스 식별자 (로그 파일명, watermark 파일명에 사용) | 문자열 | env `WORKER_ID` (기본 `rescrape-1`) |

### Docker

```bash
docker build -t rescrape-dispatcher:latest .
docker run --env-file .env.prod rescrape-dispatcher:latest python -m app

# 또는 배포 스크립트 사용
./deploy/build.sh [tag]           # 기본 태그 latest
./deploy/run.sh <worker_id>       # 예: ./deploy/run.sh rescrape-1
```

`deploy/run.sh`는 `APP_ENV`(기본 `dev`) 기준 `.env.${APP_ENV}`를 로드하고,
`~/apps/data/rescrape-dispatcher/{logs,watermark}`를 볼륨 마운트, `WATERMARK_DIR=/app/watermark`로
설정 후 `--user "$(id -u):$(id -g)"`, `--restart unless-stopped`로 `python -m app`을
실행한다(동일 이름 컨테이너가 있으면 먼저 `docker rm -f`). Dockerfile은
`python:3.12-slim` 기반(Playwright 불필요, HTTP/Solr + MySQL만 사용)이며 `CMD`/`ENTRYPOINT`는
없다.

> **DB-lookup 모드(`SOLR_DIRECT_ENABLED=false`, 기본값)에서는 `WATERMARK_DIR` 볼륨 마운트가
> 필수다** — 마운트하지 않으면 컨테이너 재시작 시 watermark가 초기화되어 마지막 동기화
> 시각을 잃는다. direct 모드는 재시작 간 상태가 없어 볼륨이 필요 없다.

## 환경 변수

`.env`(공통) 로드 후 `.env.{APP_ENV}` 로 override (`APP_ENV` 기본값 `local`). 필수 항목
누락 시 `config.validate()`가 목록을 출력하고 종료한다.

**필수 (항상)**

| 변수 | 설명 | 예시 |
|---|---|---|
| `RDS_HOST` | MySQL 호스트 | - |
| `RDS_USER` | MySQL 사용자 | - |
| `RDS_PASSWORD` | MySQL 비밀번호 | - |
| `RDS_CRAWLER_DB` | `t_crawl_url` 삽입 대상 스키마 | `crawlerdb` |

**필수 (`SOLR_DIRECT_ENABLED=false`, 즉 DB-lookup 모드일 때, `config.validate()`가 기동 시 검증)**: `DI_TNT_ID`, `DI_PROJECT_ID`, `DI_SERVER_IP`
**필수 (`TUNNEL_ENABLED=true`일 때, `config.validate()`가 기동 시 검증)**: `TUNNEL_SSH_HOST`, `TUNNEL_SSH_KEY_PATH`

> **주의**: direct 모드(`SOLR_DIRECT_ENABLED=true`)에서 `SOLR_URL`은 `config.validate()`의
> 검증 대상이 아니다 — 값이 비어 있어도 기동은 성공하고, 매 사이클 Solr 요청이 실패하며
> `DISPATCH_INTERVAL_SECONDS` 간격으로 계속 재시도되는 조용한 무한 루프가 된다(기동 시
> 즉시 실패하지 않음). direct 모드로 운영할 때는 `SOLR_URL`을 반드시 직접 확인할 것.

| 변수 | 설명 | 값 범위 / 기본값 |
|---|---|---|
| `APP_ENV` | `.env.{APP_ENV}` 선택 | `local`(기본) \| `dev` \| `prod` |
| `RDS_PORT` | MySQL 포트 (crawlerdb) | 정수, 기본 `3306` |
| `RDS_TRENDTRACKER_DB` | `t_di_config_v1` 조회 대상 스키마 이름 | 기본 `trendtracker` |
| `RDS_TRENDTRACKER_HOST` | trendtracker DB 호스트. **crawlerdb와 다른 서버일 때만 설정** | 미설정 시 `RDS_HOST`로 폴백 |
| `RDS_TRENDTRACKER_PORT` | trendtracker DB 포트 | 정수, 미설정 시 `RDS_PORT`로 폴백 |
| `RDS_TRENDTRACKER_USER` | trendtracker DB 사용자 | 미설정 시 `RDS_USER`로 폴백 |
| `RDS_TRENDTRACKER_PASSWORD` | trendtracker DB 비밀번호 | 미설정 시 `RDS_PASSWORD`로 폴백 |
| `TUNNEL_ENABLED` | SSH 터널 사용 여부 (crawlerdb/trendtracker 공통) | bool, 기본 `false` |
| `TUNNEL_SSH_HOST` | bastion 호스트 (두 DB 접속이 공유) | - |
| `TUNNEL_SSH_PORT` | SSH 포트 (공유) | 정수, 기본 `22` |
| `TUNNEL_SSH_USER` | SSH 사용자 (공유) | 기본 `ubuntu` |
| `TUNNEL_SSH_KEY_PATH` | 개인키 경로 (공유) | - |
| `TUNNEL_LOCAL_PORT` | crawlerdb 터널의 로컬 포트 | 정수, 기본 `13307` |
| `TUNNEL_TRENDTRACKER_LOCAL_PORT` | trendtracker 터널의 로컬 포트 (crawlerdb와 동시에 열려도 겹치지 않도록 분리) | 정수, 기본 `13308` |
| `WORKER_ID` | 워커 식별자 (`--worker-id`로 override) | 문자열, 기본 `rescrape-1` |
| `SOLR_DIRECT_ENABLED` | `true`=direct 모드, `false`=DB-lookup 모드 | bool, 기본 `false` |
| `SOLR_URL` | Solr 코어 URL (direct 모드) | `http://localhost:8983/solr/news` |
| `SOLR_QUERY` | Solr `q` 파라미터 (direct 모드) | 기본 `*:*` |
| `SOLR_FILTER_QUERY` | 추가 Solr `fq` 파라미터 (direct 모드) | 기본 빈 문자열 |
| `SLIDING_WINDOW_MINUTES` | `tstamp` 범위 쿼리용 슬라이딩 윈도우 크기 (권장: `DISPATCH_INTERVAL_SECONDS`/60의 2배) | 정수(분), 기본 `30` |
| `SOLR_MAX_DOCS` | 사이클당 최대 조회 URL 수 | 정수, 기본 `1000` |
| `SOLR_QUERY_BATCH_SIZE` | Solr 요청 페이지당 행 수 (커서 페이지네이션) | 정수, 기본 `100` |
| `SOLR_RESCRAPE_URL_CONTAINS` | 콤마 구분 URL 부분 문자열 패턴 (OR 결합, `url:*pattern*` fq로 변환) | 예: `naver.com,daum.net` |
| `DI_TNT_ID` | 테넌트 id (`t_di_config_v1` 조회 키) | - |
| `DI_PROJECT_ID` | 프로젝트 id (조회 키) | - |
| `DI_SERVER_IP` | DI 서버 IP (조회 키) | - |
| `HTTP_VERIFY_SSL` | Solr HTTP 호출 TLS 검증 | bool, 기본 `true` |
| `SOLR_RESCRAPE_MAX_WATERMARK_LOOKBACK_MINUTES` | 다운타임 후 최대 소급 처리 시간 (DB-lookup 모드만) | 정수(분), 기본 `1440` |
| `WATERMARK_DIR` | watermark 파일 저장 디렉토리 (재시작 간 지속하려면 볼륨 마운트) | 경로, 기본 `./watermark` |
| `DISPATCH_INTERVAL_SECONDS` | 메인 루프 주기 | 정수(s), 기본 `300` |
| `RESCRAPE_PRIORITY` | 재삽입 URL에 부여할 우선순위 (discovery-worker 기본값 0보다 높게) | 정수, 기본 `5` |
| `LOG_DIR` | 로그 디렉토리 | 경로, 기본 `./logs` |
| `LOG_LEVEL` | 로그 레벨 | 예: `DEBUG`/`INFO`/`WARNING`/`ERROR`, 기본 `INFO` |
| `LOG_ROTATION` | 로테이션 방식 | `daily` \| size 기반, 기본 `daily` |
| `LOG_RETAIN_DAYS` | 보관 일수 (daily) | 정수, 기본 `30` |
| `LOG_BACKUP_COUNT` | 보관 파일 개수 (size) | 정수, 기본 `10` |
| `HEARTBEAT_INTERVAL_SECONDS` | 하트비트 로그 주기 | 정수(s), 기본 `60` |

## 유틸리티 스크립트 (`scripts/`)

`run_once.py`를 제외하면 모두 읽기 전용 진단 스크립트다.

> `check_dispatch.py`/`run_once.py`는 `SolrClient.query_rescrape_candidates()`가 watermark
> 기능 추가로 2-tuple에서 3-tuple 반환으로 바뀐 뒤 언패킹이 갱신되지 않아 Solr 조회
> 단계에서 항상 예외(`too many values to unpack`)로 실패하던 버그가 있었다 — 이번에 수정함.

| 스크립트 | 인자 | 설명 |
|---|---|---|
| `check_db.py` | 없음 | `crawlerdb`(`t_crawl_url` 건수), `trendtracker`(`t_di_config_v1` 건수) 연결 확인 |
| `check_di_config.py` | 없음 | `DI_TNT_ID`/`DI_PROJECT_ID`/`DI_SERVER_IP`로 `t_di_config_v1` 조회, `use_yn != 'Y'`나 `solr_url` 비어있으면 경고 후 exit 1 |
| `check_solr.py` | 없음 | Solr URL 조회(direct/DB-lookup) 후 `/admin/ping`, 전체 문서 수 조회 |
| `check_solr_count.py` | `[--no-window]` | 현재 쿼리 + 슬라이딩 윈도우 필터 기준 문서 수 조회, `--no-window`로 전체 카운트 |
| `check_dispatch.py` | `[--limit N]` (기본 20) | 드라이런: Solr에서 재수집 후보 URL을 최대 N개 조회해 출력, **DB 쓰기 없음** |
| `run_once.py` | 없음 | 전체 dispatch 사이클을 1회 실행 후 종료 — **`t_crawl_url`에 실제로 쓴다**, 수동 테스트용 |

## 주요 라이브러리

`SQLAlchemy`/`PyMySQL`(DB), `sshtunnel`/`paramiko`(SSH 터널),
`httpx`(Solr HTTP 호출), `python-dotenv`(설정). FastAPI/Celery/APScheduler는 사용하지 않으며
스케줄링은 `time.sleep` 기반 자체 루프 + 하트비트로 구현되어 있다.
