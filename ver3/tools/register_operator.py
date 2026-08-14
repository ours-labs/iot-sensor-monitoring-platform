"""メールアドレスと入力者IDをPostgreSQL上で紐付ける管理者用ツール。"""

import argparse
from pathlib import Path
import re
import sys


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from database import PostgresRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ver3利用者登録")
    parser.add_argument("email")
    parser.add_argument("operator_id")
    parser.add_argument("display_name")
    args = parser.parse_args()
    email = args.email.strip().lower()
    operator_id = args.operator_id.strip().upper()
    if not re.fullmatch(r"TK[0-9]{6}", operator_id):
        parser.error("operator_idはTK+数字6桁です")
    repository = PostgresRepository()
    repository.open()
    try:
        with repository.connection() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO operator_identities(
                    principal_email, operator_id, display_name
                ) VALUES (%s, %s, %s)
                ON CONFLICT (principal_email) DO UPDATE SET
                    operator_id = EXCLUDED.operator_id,
                    display_name = EXCLUDED.display_name,
                    active = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (email, operator_id, args.display_name),
            )
    finally:
        repository.close()
    print("利用者対応を保存しました。メールアドレスや入力者IDをGitへ保存しないでください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
