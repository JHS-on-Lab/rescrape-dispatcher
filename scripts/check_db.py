"""
DB 연결 확인 스크립트.

실행:
  python scripts/check_db.py

확인 항목:
  1. crawlerdb 스키마 접속 (t_crawl_url 행 수)
  2. trendtracker 스키마 접속 (t_di_config_v1 행 수)

두 스키마는 서로 다른 DB 서버에 있을 수 있어 별도 엔진으로 각각 접속한다
(app/repository/db.py 의 db_context()/trendtracker_db_context()).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from app import config
from app.repository.db import db_context, trendtracker_db_context


def main() -> None:
    tunnel_info = f" (SSH 터널: {config.TUNNEL_SSH_HOST})" if config.TUNNEL_ENABLED else ""
    print(f"crawlerdb    : {config.RDS_HOST}:{config.RDS_PORT} / DB={config.RDS_CRAWLER_DB}{tunnel_info}")
    print(f"trendtracker : {config.RDS_TRENDTRACKER_HOST}:{config.RDS_TRENDTRACKER_PORT} / DB={config.RDS_TRENDTRACKER_DB}{tunnel_info}")
    print()

    print(f"1. {config.RDS_CRAWLER_DB} 스키마 접속...")
    try:
        with db_context() as engine:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT COUNT(*) FROM t_crawl_url")).fetchone()
                print(f"   t_crawl_url 행 수: {row[0]:,}")
    except Exception as e:
        print(f"   [오류] {e}")
        sys.exit(1)

    print(f"2. {config.RDS_TRENDTRACKER_DB} 스키마 접속...")
    try:
        with trendtracker_db_context() as engine:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT COUNT(*) FROM t_di_config_v1")).fetchone()
                print(f"   t_di_config_v1 행 수: {row[0]:,}")
    except Exception as e:
        print(f"   [오류] {e}")
        sys.exit(1)

    print()
    print("DB 연결 성공.")


if __name__ == "__main__":
    main()
