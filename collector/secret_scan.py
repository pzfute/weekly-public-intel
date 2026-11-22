#!/usr/bin/env python3
"""Refuse to commit private or credential-like content from a public repo."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Filename / path deny
PATH_DENY = re.compile(
    r"(store_intelligence|shop_secret|dianxiaomi|qwen_internal|qwen|千问|店小秘|csp_diag|private_diag|\.env$|\.pem$|\.key$|cookie|chrome-profile)",
    re.I,
)
# Content deny. Keep this list in the scanner file; matches here are expected.
CONTENT_DENY = [
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"ghs_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"x-access-token:[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"][^'\"]{8,}"),
    re.compile(r"(?i)(password|secret|token)\s*[:=]\s*['\"][^'\"]{8,}"),
]

SKIP_DIRS = {".git", "__pycache__"}
# Allow the scanner itself and docs that mention the policy.
SKIP_FILES = {"secret_scan.py"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    args = p.parse_args()
    root = Path(args.root)
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(root))
        if path.name in SKIP_FILES:
            continue
        if PATH_DENY.search(rel):
            hits.append(f"path:{rel}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            # skip binary
            continue
        for cre in CONTENT_DENY:
            if cre.search(text):
                hits.append(f"content:{rel}:{cre.pattern}")
                break
    if hits:
        print("SECRET_SCAN_FAILED")
        for h in hits:
            print(h)
        return 2
    print("SECRET_SCAN_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
