"""公開前に個人・運用情報と生成物の混入を検査する。"""

from pathlib import Path
import re
import subprocess
import sys
import os


ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
SKIP_PARTS = {'.git', '.gradle', 'build'}
PRIVATE_IPV4 = re.compile(
    r'(?<![0-9])(?:'
    r'10\.(?:[0-9]{1,3}\.){2}[0-9]{1,3}|'
    r'100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])'
    r'\.[0-9]{1,3}\.[0-9]{1,3}|'
    r'172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|'
    r'192\.168\.[0-9]{1,3}\.[0-9]{1,3}'
    r')(?![0-9])'
)
EMAIL = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.IGNORECASE)
ALLOWED_EMAIL_DOMAINS = {
    'example.com',
    'example.invalid',
    'users.noreply.github.com',
}
TEXT_RULES = {
    'private-ip': PRIVATE_IPV4,
    'student-id': re.compile(r'\bTK[0-9]{6}\b', re.IGNORECASE),
    'personal-home': re.compile(r'/home/[A-Za-z0-9_-]+', re.IGNORECASE),
    'windows-user-home': re.compile(
        r'[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+', re.IGNORECASE
    ),
    'prohibited-attribution-trace': re.compile(
        r'\b(?:co' r'dex|chat' r'gpt|open' r'ai|clau' r'de|'
        r'ai[- ]?(?:assistant|generated|assisted))\b',
        re.IGNORECASE,
    ),
}
for index, term in enumerate(filter(None, os.environ.get('PRIVATE_SCAN_TERMS', '').split(',')), 1):
    TEXT_RULES[f'private-term-{index}'] = re.compile(re.escape(term.strip()), re.IGNORECASE)
FORBIDDEN_SUFFIXES = {'.pyc', '.apk', '.db'}


def private_export_ignores():
    manifest = ROOT / '.public-export-ignore'
    try:
        lines = manifest.read_text(encoding='utf-8').splitlines()
    except OSError:
        return set()
    return {
        line.strip().replace('\\', '/')
        for line in lines
        if line.strip() and not line.lstrip().startswith('#')
    }


def publish_candidate_files():
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode('utf-8') for item in result.stdout.split(b'\0') if item]


def main():
    findings = []
    excluded = private_export_ignores()
    for path in publish_candidate_files():
        relative = path.relative_to(ROOT)
        if (
            path == SELF
            or relative.as_posix() in excluded
            or any(part in SKIP_PARTS for part in relative.parts)
        ):
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name == '.env':
            findings.append(f'forbidden-file: {relative}')
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in TEXT_RULES.items():
            for match in pattern.finditer(text):
                line = text.count('\n', 0, match.start()) + 1
                findings.append(f'{name}: {relative}:{line}')
        for match in EMAIL.finditer(text):
            domain = match.group(0).rsplit('@', 1)[1].lower()
            if domain not in ALLOWED_EMAIL_DOMAINS:
                line = text.count('\n', 0, match.start()) + 1
                findings.append(f'email: {relative}:{line}')

    if findings:
        print('[PRIV-E001] 公開禁止情報を検出しました。')
        print('\n'.join(findings))
        return 1
    print('Public release scan: OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
