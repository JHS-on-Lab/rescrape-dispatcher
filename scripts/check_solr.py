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


def _get_solr_url() -> str:
    if config.SOLR_DIRECT_ENABLED:
        if not config.SOLR_URL:
            print("[오류] SOLR_DIRECT_ENABLED=true 이지만 SOLR_URL 이 설정되지 않았습니다.")
            sys.exit(1)
        print("[모드] 직접 접속 (SOLR_DIRECT_ENABLED=true)")
        return config.SOLR_URL

    if not (config.DI_TNT_ID and config.DI_PROJECT_ID and config.DI_SERVER_IP):
        print("[오류] DI_TNT_ID / DI_PROJECT_ID / DI_SERVER_IP 를 .env 에 설정하세요.")
        sys.exit(1)

    print(
        f"[모드] DB 조회 "
        f"(tnt_id={config.DI_TNT_ID} project_id={config.DI_PROJECT_ID} di_server_ip={config.DI_SERVER_IP})"
    )
    from app.repository.db import db_context
    from app.repository.di_config_repo import DiConfigRepo

    with db_context() as engine:
        di_config = DiConfigRepo(engine).get_config()

    if not di_config:
        print("[오류] t_di_config_v1 에서 조건에 맞는 행이 없거나 use_yn='N' 입니다.")
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
