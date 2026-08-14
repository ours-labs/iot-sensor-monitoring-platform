"""サーバー上で共有管理パスワードのハッシュだけをweb.envへ保存する。"""
from __future__ import annotations

import argparse
from getpass import getpass
import os
from pathlib import Path
import re
import tempfile

from werkzeug.security import generate_password_hash


def update_env(path: Path, password_hash: str) -> None:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    line = "ADMIN_PASSWORD_HASH=" + password_hash
    if re.search(r"(?m)^ADMIN_PASSWORD_HASH=.*$", original):
        updated = re.sub(r"(?m)^ADMIN_PASSWORD_HASH=.*$", line, original)
    else:
        updated = original.rstrip("\n") + ("\n" if original else "") + line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    first = getpass("新しい共有管理パスワード: ")
    second = getpass("確認のため再入力: ")
    if first != second:
        raise SystemExit("[CFG-W008] パスワードが一致しません")
    if len(first) < 12:
        raise SystemExit("[CFG-W009] 共有管理パスワードは12文字以上にしてください")
    update_env(args.env_file, generate_password_hash(first))
    print("ADMIN_PASSWORD_HASHをハッシュ形式で更新しました。平文は保存していません。")


if __name__ == "__main__":
    main()
