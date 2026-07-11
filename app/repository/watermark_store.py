"""
워터마크(last_synced_at) 로컬 파일 저장소.

trendtracker.t_di_config_v1 은 외부 소유 테이블이라 컬럼을 추가할 권한이 없어
DB 에는 워터마크를 저장할 수 없다. 대신 DI 설정(tnt_id/project_id/di_server_ip)
+ WORKER_ID 조합별로 로컬 파일에 저장한다.

WORKER_ID 를 경로에 포함하는 이유: 같은 DI 설정으로 여러 컨테이너를 동시에
띄우는 멀티 인스턴스 구성에서, 워터마크 디렉터리를 컨테이너 간에 공유 마운트해도
컨테이너마다 파일 자체가 달라져 "읽기→비교→쓰기" 레이스가 애초에 발생하지 않는다.
google_news.py 의 Chrome 프로필 디렉터리를 WORKER_ID 로 분리한 것과 동일한 이유다.
(WORKER_ID 를 안 붙이고 파일을 공유했다면, 두 컨테이너가 거의 동시에 갱신할 때
나중 쓰기가 더 앞선 값을 덮어써 워터마크가 역행할 수 있었다 — 최악의 경우에도
데이터 유실은 아니고 다음 사이클에 그만큼 더 넓게 재조회할 뿐이지만, 굳이 감수할
필요 없는 레이스라 경로 분리로 원천 차단한다.)

컨테이너 재시작에도 살아남으려면 WATERMARK_DIR 을 호스트 볼륨으로 마운트해야
한다 (다른 워커들의 chrome_profile/logs 마운트와 동일한 패턴). 마운트하지 않거나
컨테이너가 완전히 새로 생성되면 워터마크가 초기화되고 순수 슬라이딩 윈도우로
동작할 뿐이니, 최악의 경우에도 기존 동작으로 폴백하는 것 — 데이터 오염은 없다.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from app import config

_log = logging.getLogger(__name__)


def _path_for(tnt_id: str, project_id: str, di_server_ip: str, worker_id: str) -> Path:
    safe = f"{tnt_id}_{project_id}_{di_server_ip}_{worker_id}".replace("/", "_")
    return Path(config.WATERMARK_DIR) / f"{safe}.json"


def load(tnt_id: str, project_id: str, di_server_ip: str, worker_id: str) -> datetime | None:
    """저장된 워터마크를 읽는다. 파일이 없거나 손상됐으면 None (순수 슬라이딩 윈도우로 폴백)."""
    path = _path_for(tnt_id, project_id, di_server_ip, worker_id)
    try:
        raw = json.loads(path.read_text()).get("last_synced_at")
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        _log.warning(f"워터마크 파일 파싱 실패, 무시하고 진행: {path}")
        return None


def advance(
    tnt_id: str, project_id: str, di_server_ip: str, worker_id: str,
    new_synced_at: datetime,
) -> None:
    """워터마크를 new_synced_at 으로 전진시킨다. 기존 값보다 과거면 무시(역행 방지).

    경로가 WORKER_ID 로 분리돼 있어 동일 워커 프로세스 내에서만 호출되는 한
    read-compare-write 레이스가 없다 (다른 프로세스가 같은 파일을 건드릴 수 없음).
    """
    current = load(tnt_id, project_id, di_server_ip, worker_id)
    if current is not None and new_synced_at <= current:
        return

    path = _path_for(tnt_id, project_id, di_server_ip, worker_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_synced_at": new_synced_at.isoformat()}))
    os.replace(tmp, path)  # 원자적 교체 — 쓰다 죽어도 기존 파일은 안전
