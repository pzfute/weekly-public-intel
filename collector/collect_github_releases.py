#!/usr/bin/env python3
"""Collect public GitHub releases for a small allowlist.

Uses GITHUB_TOKEN when present (GitHub Actions). Unauthenticated calls are
rate-limited (60/hour/IP) and must be recorded accurately on 403.
No model calls, no paid APIs.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ledger import planned_column

TZ = dt.timezone(dt.timedelta(hours=8))
# Small public allowlist only. Not a ranking and not a sales claim.
REPOS = [
    "modelcontextprotocol/servers",
    "huggingface/transformers",
    "cli/cli",
]


def now_cn() -> dt.datetime:
    return dt.datetime.now(TZ)


def headers() -> dict:
    h = {
        "User-Agent": "public-intel-collect/1.0",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers=headers())
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return {
                "ok": True,
                "http_status": getattr(resp, "status", 200),
                "url": url,
                "elapsed_s": round(time.time() - t0, 3),
                "body": body,
                "headers": {k.lower(): v for k, v in resp.headers.items()},
                "error": None,
            }
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        return {
            "ok": False,
            "http_status": e.code,
            "url": url,
            "elapsed_s": round(time.time() - t0, 3),
            "body": body,
            "headers": {k.lower(): v for k, v in (e.headers.items() if e.headers else [])},
            "error": f"HTTPError {e.code}",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "http_status": None,
            "url": url,
            "elapsed_s": round(time.time() - t0, 3),
            "body": b"",
            "headers": {},
            "error": f"{type(e).__name__}: {e}",
        }


def load_seen(path: Path) -> set[str]:
    seen: set[str] = set()
    if not path.exists():
        return seen
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("id"):
                seen.add(rec["id"])
    return seen


def append_ledger(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "run_id", "planned_at", "actual_start", "actual_end", "source",
        "http_status", "result_count", "new_count", "dup_count",
        "failed_count", "cost_usd", "notes",
    ]
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="out")
    p.add_argument("--run-id", default="")
    p.add_argument("--planned-at", default="")
    p.add_argument("--cost-usd", default="0")
    p.add_argument("--trigger", default="")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records_path = out / "records.jsonl"
    ledger_path = out / "ledger.csv"
    start = now_cn()
    run_id = args.run_id or start.strftime("run_%Y%m%d_%H%M%S")
    seen = load_seen(records_path)
    records: list[dict] = []
    http_notes: list[str] = []
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    authed = bool(token)

    def scrub(value):
        if not token or value is None:
            return value
        if isinstance(value, str):
            return value.replace(token, "[redacted]")
        return value

    for repo in REPOS:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=3"
        r = fetch(url)
        http_notes.append(f"{repo}={r['http_status']}")
        if r["http_status"] == 403:
            records.append({
                "id": f"gh:releases:{repo}:rate_or_denied",
                "source": "github_releases",
                "source_url": url,
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": 403,
                "status": "RATE_LIMITED" if b"rate limit" in (r["body"] or b"").lower() else "FAILED",
                "note": r["error"] or "HTTP 403",
                "raw": scrub(r["body"][:500].decode("utf-8", "replace") if r["body"] else None),
            })
            continue
        if not (r["ok"] and r["http_status"] == 200):
            records.append({
                "id": f"gh:releases:{repo}",
                "source": "github_releases",
                "source_url": url,
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": r["http_status"],
                "status": "FAILED",
                "note": r["error"] or "non-200",
                "raw": scrub(r["body"][:500].decode("utf-8", "replace") if r["body"] else None),
            })
            continue
        try:
            releases = json.loads(r["body"].decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            records.append({
                "id": f"gh:releases:{repo}",
                "source": "github_releases",
                "source_url": url,
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": r["http_status"],
                "status": "FAILED",
                "note": f"json parse: {e}",
                "raw": None,
            })
            continue
        if not releases:
            records.append({
                "id": f"gh:releases:{repo}:empty",
                "source": "github_releases",
                "source_url": f"https://github.com/{repo}/releases",
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": 200,
                "status": "EMPTY",
                "note": "no releases",
                "raw": [],
            })
            continue
        for rel in releases:
            tag = rel.get("tag_name") or rel.get("id") or "unknown"
            html = rel.get("html_url") or f"https://github.com/{repo}/releases"
            # Keep body short in the stored raw to avoid huge commits
            slim = {
                "id": rel.get("id"),
                "tag_name": rel.get("tag_name"),
                "name": rel.get("name"),
                "draft": rel.get("draft"),
                "prerelease": rel.get("prerelease"),
                "html_url": html,
                "published_at": rel.get("published_at"),
                "body_excerpt": (rel.get("body") or "")[:500],
            }
            records.append({
                "id": f"gh:release:{repo}:{tag}",
                "source": "github_releases",
                "source_url": html,
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": 200,
                "status": "OK",
                "note": f"repo={repo}",
                "raw": slim,
            })
        time.sleep(0.3)

    new_count = dup_count = failed_count = 0
    with records_path.open("a", encoding="utf-8") as f:
        for rec in records:
            if rec["status"] in ("FAILED", "RATE_LIMITED"):
                failed_count += 1
            if rec["id"] in seen:
                dup_count += 1
                rec["dedup"] = "duplicate"
            else:
                new_count += 1
                rec["dedup"] = "new"
                seen.add(rec["id"])
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    end = now_cn()
    notes = f"allowlist={len(REPOS)}; authed={authed}; token_not_stored=true"
    if args.trigger:
        notes += f"; trigger={args.trigger}"
    notes += "; planned_at=timestamp" if planned_column(args.planned_at) else "; planned_at=blank"
    append_ledger(ledger_path, {
        "run_id": run_id,
        "planned_at": planned_column(args.planned_at),
        "actual_start": start.isoformat(),
        "actual_end": end.isoformat(),
        "source": "github_releases",
        "http_status": ";".join(http_notes),
        "result_count": len(records),
        "new_count": new_count,
        "dup_count": dup_count,
        "failed_count": failed_count,
        "cost_usd": args.cost_usd,
        "notes": notes,
    })
    summary = {
        "run_id": run_id,
        "planned_at": planned_column(args.planned_at),
        "planned_at_kind": "schedule_timestamp" if planned_column(args.planned_at) else "not_scheduled",
        "actual_start": start.isoformat(),
        "actual_end": end.isoformat(),
        "result_count": len(records),
        "new_count": new_count,
        "dup_count": dup_count,
        "failed_count": failed_count,
        "cost_usd": args.cost_usd,
        "model_inference": 0,
        "paid_api_calls": 0,
        "authed": authed,
    }
    (out / f"summary_{run_id}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
