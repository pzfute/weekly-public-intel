#!/usr/bin/env python3
"""Collect long-tail product *candidates* from public Stack Exchange Q&A.

This records discussion evidence only. It does not measure search demand,
live listings, or completed orders. eBay Browse API is not used (previously HTTP 403).
No model calls, no paid APIs.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ledger import planned_column

TZ = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "public-intel-collect/1.0"}
# Conservative public queries. These are discussion threads, not sales.
SEARCHES = [
    {"site": "diy", "q": "replacement part"},
    {"site": "mechanics", "q": "replacement part"},
    {"site": "hardwarerecs", "q": "compatible replacement"},
]
MODEL_RE = re.compile(r"\b(?:[A-Z]{1,3}\d{2,}[A-Z0-9\-]*|[A-Z]{2,}\-\d{2,}[A-Z0-9\-]*)\b")


def now_cn() -> dt.datetime:
    return dt.datetime.now(TZ)


def fetch(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers=UA)
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


def extract_model(text: str) -> str:
    found = MODEL_RE.findall(text or "")
    return ",".join(list(dict.fromkeys(found))[:5]) if found else "未提取"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="out")
    p.add_argument("--run-id", default="")
    p.add_argument("--planned-at", default="")
    p.add_argument("--cost-usd", default="0")
    p.add_argument("--trigger", default="")
    p.add_argument("--pagesize", type=int, default=5)
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

    for spec in SEARCHES:
        qs = urllib.parse.urlencode({
            "order": "desc",
            "sort": "activity",
            "site": spec["site"],
            "q": spec["q"],
            "pagesize": args.pagesize,
            "filter": "default",
        })
        url = f"https://api.stackexchange.com/2.3/search/advanced?{qs}"
        r = fetch(url)
        tag = f"se:{spec['site']}"
        http_notes.append(f"{tag}={r['http_status']}")
        if not (r["ok"] and r["http_status"] == 200):
            records.append({
                "id": f"se:search:{spec['site']}:{spec['q']}",
                "source": "stackexchange",
                "source_url": url,
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": r["http_status"],
                "status": "FAILED",
                "note": r["error"] or "non-200",
                "raw": (r["body"][:500].decode("utf-8", "replace") if r["body"] else None),
                "candidate": None,
            })
            continue
        try:
            data = json.loads(r["body"].decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            records.append({
                "id": f"se:search:{spec['site']}:{spec['q']}",
                "source": "stackexchange",
                "source_url": url,
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": r["http_status"],
                "status": "FAILED",
                "note": f"json parse: {e}",
                "raw": None,
                "candidate": None,
            })
            continue
        items = data.get("items") or []
        if not items:
            records.append({
                "id": f"se:search:{spec['site']}:{spec['q']}:empty",
                "source": "stackexchange",
                "source_url": url,
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": r["http_status"],
                "status": "EMPTY",
                "note": f"quota_remaining={data.get('quota_remaining')}",
                "raw": {"quota_remaining": data.get("quota_remaining")},
                "candidate": None,
            })
            continue
        for it in items:
            qid = it.get("question_id")
            title = it.get("title") or ""
            link = it.get("link") or url
            rec = {
                "id": f"se:{spec['site']}:question:{qid}",
                "source": "stackexchange",
                "source_url": link,
                "collected_at": start.isoformat(),
                "run_id": run_id,
                "http_status": r["http_status"],
                "status": "OK",
                "note": f"site={spec['site']}; q={spec['q']}; quota_remaining={data.get('quota_remaining')}",
                "raw": it,
                "candidate": {
                    "product_name": title,
                    "model": extract_model(title),
                    "source_name": f"Stack Exchange / {spec['site']}",
                    "source_url": link,
                    "collected_at": start.isoformat(),
                    "demand_evidence": "discussion",
                    "demand_evidence_note": "Public Q&A thread. This is discussion activity, not search volume, not a live listing, and not a completed order.",
                    "competition": "未验证",
                    "real_supply": "未验证",
                    "verification_status": "候选",
                },
            }
            records.append(rec)
        time.sleep(0.4)

    new_count = dup_count = failed_count = 0
    with records_path.open("a", encoding="utf-8") as f:
        for rec in records:
            if rec["status"] == "FAILED":
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
    notes = "discussion only; not sales or demand verification; eBay not enabled; verification_status=候选"
    if args.trigger:
        notes += f"; trigger={args.trigger}"
    notes += "; planned_at=timestamp" if planned_column(args.planned_at) else "; planned_at=blank"
    append_ledger(ledger_path, {
        "run_id": run_id,
        "planned_at": planned_column(args.planned_at),
        "actual_start": start.isoformat(),
        "actual_end": end.isoformat(),
        "source": "stackexchange",
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
        "demand_type": "discussion",
    }
    (out / f"summary_{run_id}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
