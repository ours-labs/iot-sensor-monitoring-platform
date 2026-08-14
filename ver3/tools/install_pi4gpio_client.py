"""検証済みpi4gpio-client wheelをSHA-256固定でVer3 venvへ導入する。"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv-python", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--expected-version", default="0.1.1")
    args = parser.parse_args()

    if not args.venv_python.is_file():
        raise SystemExit("[CFG-C010] Ver3 venvのPythonが見つかりません")
    if not args.wheel.is_file() or args.wheel.suffix != ".whl":
        raise SystemExit("[CFG-C011] pi4gpio-client wheelが見つかりません")
    expected = args.sha256.strip().lower()
    actual = sha256(args.wheel)
    if len(expected) != 64 or actual != expected:
        raise SystemExit(f"[DEP-E001] wheel SHA-256不一致: actual={actual}")

    subprocess.run([
        str(args.venv_python), "-m", "pip", "install", "--no-deps",
        "--force-reinstall", str(args.wheel),
    ], check=True)
    check = (
        "import importlib.metadata as m; import pi4gpio_client; "
        "print(m.version('pi4gpio-client'))"
    )
    result = subprocess.run(
        [str(args.venv_python), "-c", check], check=True, text=True,
        capture_output=True,
    )
    installed = result.stdout.strip()
    if installed != args.expected_version:
        raise SystemExit(
            f"[DEP-E002] pi4gpio-client版不一致: {installed} "
            f"!= {args.expected_version}"
        )
    print(f"pi4gpio-client {installed} installed; sha256={actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
