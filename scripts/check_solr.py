"""
Solr 연결 확인 스크립트.

실행:
  python scripts/check_solr.py

접속 모드 (.env 설정에 따라 자동 선택):
  SOLR_DIRECT_ENABLED=true  → SOLR_URL 로 직접 접속
  SOLR_DIRECT_ENABLED=false → DI_* 조건으로 trendtracker.t_di_config_v1 조회 후 접속

확인 항목:
  1. solr_url 결정 (직접 or DB 조회)
  2. Solr ping (/admin/ping)
  3. 저장된 문서 수 (q=*:*, rows=0)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from app import config
from app.repository.db import db_context
from app.scheduling.dispatcher import resolve_di_config


def _get_solr_url() -> str:
    """resolve_di_config() 로 직접/DB 조회 모드를 판단해 solr_url 을 반환한다.

    dispatcher.py 와 동일한 로직을 재사용한다 — 이전에는 이 스크립트만 별도로
    구현하고 있어서 dispatcher.py 쪽 로직이 바뀌면 스크립트가 따라가지 못했다.
    """
    try:
        if config.SOLR_DIRECT_ENABLED:
            print("[모드] 직접 접속 (SOLR_DIRECT_ENABLED=true)")
            di_config = resolve_di_config()
        else:
            print(
                f"[모드] DB 조회 "
                f"(tnt_id={config.DI_TNT_ID} project_id={config.DI_PROJECT_ID}"
                f" di_server_ip={config.DI_SERVER_IP})"
            )
            with db_context() as engine:
                di_config = resolve_di_config(engine)
    except RuntimeError as e:
        print(f"[오류] {e}")
        sys.exit(1)

    if not di_config.solr_url:
        print("[오류] solr_url 이 비어 있습니다. SOLR_URL(직접 모드) 또는 "
              "t_di_config_v1.solr_url(DB 조회 모드) 설정을 확인하세요.")
        sys.exit(1)

    return di_config.solr_url


def main() -> None:
    solr_url = _get_solr_url().rstrip("/")
    print(f"Solr URL : {solr_url}")
    print()

    print("1. Ping 테스트...")
    try:
        resp = httpx.get(f"{solr_url}/admin/ping", params={"wt": "json"}, timeout=5)
        resp.raise_for_status()
        print("   상태: OK")
    except httpx.ConnectError:
        print(f"   [오류] {solr_url} 에 연결할 수 없습니다.")
        sys.exit(1)
    except Exception as e:
        print(f"   [오류] {e}")
        sys.exit(1)

    print("2. 문서 수 확인...")
    try:
        resp = httpx.get(
            f"{solr_url}/select",
            params={"q": "*:*", "rows": "0", "wt": "json"},
            timeout=5,
        )
        resp.raise_for_status()
        num_found = resp.json().get("response", {}).get("numFound", 0)
        print(f"   저장된 문서 수: {num_found:,}건")
    except Exception as e:
        print(f"   [오류] {e}")
        sys.exit(1)

    print()
    print("Solr 연결 성공.")


if __name__ == "__main__":
    main()
