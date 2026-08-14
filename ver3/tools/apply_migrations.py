"""管理者がVer3 PostgreSQLの未適用migrationを順番に適用する。"""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from database import apply_migrations  # noqa: E402
from system_identity import SYSTEM_VERSION  # noqa: E402

if __name__ == "__main__":
    print(f"Ver.{SYSTEM_VERSION} PostgreSQL migrationsを確認します")
    applied = apply_migrations()
    print("applied=" + (",".join(applied) if applied else "none"))
