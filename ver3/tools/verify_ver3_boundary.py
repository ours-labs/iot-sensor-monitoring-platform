"""Verify the Ver3-only version boundary and publication safety."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from public_release_scan import (
    ALLOWED_EMAIL_DOMAINS,
    EMAIL,
    TEXT_RULES,
    private_export_ignores,
)


REPO = Path(__file__).resolve().parents[2]
FORBIDDEN_FILES = {".env", "local.properties", "secrets.properties"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".apk", ".pid"}
PRIVATE_PATTERNS = TEXT_RULES
SELF = Path(__file__).resolve()
PUBLIC_SCANNER = (Path(__file__).parent / "public_release_scan.py").resolve()


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={REPO.as_posix()}", *args],
        cwd=REPO,
        check=check,
        capture_output=True,
        text=True,
    )


def release_candidate_files() -> list[Path]:
    """追跡済みとignoreされていない未追跡ファイルを検査する。"""
    result = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return [REPO / item for item in result.stdout.split("\0") if item]


def ref_exists(ref: str) -> bool:
    return git("rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def main() -> int:
    errors: list[str] = []
    excluded = private_export_ignores()

    for excluded_version in (
        "legacy-v1",
        "legacy-v2",
        "SensorDataApp",
        "SensorDataApp-v2",
    ):
        if (REPO / excluded_version).exists():
            errors.append(
                f"[VER-E101] Ver3-only snapshot contains {excluded_version}"
            )

    if (REPO / "ver3" / "VERSION").read_text(encoding="utf-8").strip() != "3.0.0":
        errors.append("[VER-E103] ver3/VERSION is not 3.0.0")

    for path in release_candidate_files():
        relative = path.relative_to(REPO)
        if (
            relative.as_posix() in excluded
            or path.resolve() in {SELF, PUBLIC_SCANNER}
        ):
            continue
        if path.name in FORBIDDEN_FILES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"[PRIV-E101] 追跡禁止ファイル: {relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, pattern in PRIVATE_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"[PRIV-E102] {name}: {relative}")
        for match in EMAIL.finditer(text):
            domain = match.group(0).rsplit("@", 1)[1].lower()
            if domain not in ALLOWED_EMAIL_DOMAINS:
                errors.append(f"[PRIV-E102] email: {relative}")
                break

    if errors:
        print("\n".join(errors))
        return 1
    print("Ver3 boundary verification: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
