"""
trendtracker.t_di_config_v1 조회 확인 스크립트.

실행:
  python scripts/check_di_config.py

.env 의 DI_TNT_ID / DI_PROJECT_ID / DI_SERVER_IP 로 조회해
Solr 접속 정보를 포함한 전체 결과를 출력한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import config
from app.repository.db import db_context
from app.repository.di_config_repo import DiConfigRepo
from sqlalchemy import text


def main() -> None:
    print(f"조회 조건")
    print(f"  tnt_id       : {config.DI_TNT_ID}")
    print(f"  project_id   : {config.DI_PROJECT_ID}")
    print(f"  di_server_ip : {config.DI_SERVER_IP}")
    print(f"  schema       : {config.RDS_TRENDTRACKER_DB}")
    print()

    with db_context() as engine:
        with engine.connect() as conn:
            row = conn.execute(
                text(f"""
                    SELECT *
                    FROM {config.RDS_TRENDTRACKER_DB}.t_di_config_v1
                    WHERE tnt_id       = :tnt_id
                      AND project_id   = :project_id
                      AND di_server_ip = :di_server_ip
                    LIMIT 1
                """),
                {
                    "tnt_id":       config.DI_TNT_ID,
                    "project_id":   config.DI_PROJECT_ID,
                    "di_server_ip": config.DI_SERVER_IP,
                },
            ).fetchone()

    if row is None:
        print("[없음] 조건에 맞는 행이 없습니다.")
        sys.exit(1)

    mapping = dict(row._mapping)
    print("조회 결과")
    print("-" * 50)
    for k, v in mapping.items():
        print(f"  {k:<25}: {v}")
    print()

    solr_url = mapping.get("solr_url")
    use_yn   = mapping.get("use_yn")

    if use_yn != "Y":
        print(f"[경고] use_yn='{use_yn}' — 비활성 행입니다.")
        sys.exit(1)

    if not solr_url:
        print("[경고] solr_url 이 비어 있습니다.")
        sys.exit(1)

    print(f"Solr URL : {solr_url}")
    print("조회 성공.")


if __name__ == "__main__":
    main()
