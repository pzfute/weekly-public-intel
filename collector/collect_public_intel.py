#!/usr/bin/env python3
"""Collect public HN + Hugging Face signals. No model calls, no paid APIs."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from ledger import planned_column

TZ = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "public-intel-collect/1.0"}


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
        "run_id",
        "planned_at",
        "actual_start",
        "actual_end",
        "source",
        "http_status",
        "result_count",
        "new_count",
        "dup_count",
        "failed_count",
        "cost_usd",
        "notes",
    ]
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="out", help="output directory")
    p.add_argument("--run-id", default="")
    p.add_argument("--planned-at", default="", help="planned time ISO, Asia/Shanghai")
    p.add_argument("--hn-items", type=int, default=3)
    p.add_argument("--hf-limit", type=int, default=3)
    p.add_argument("--cost-usd", default="0")
    p.add_argument("--trigger", default="", help="schedule or workflow_dispatch or manual; dispatch does not count as a timed test")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records_path = out / "records.jsonl"
    ledger_path = out / "ledger.csv"
    start = now_cn()
    run_id = args.run_id or start.strftime("run_%Y%m%d_%H%M%S")
    seen = load_seen(records_path)
    records: list[dict] = []

    def add(source, source_url, uid, raw, http_status, status, note, extra=None):
        rec = {
            "id": uid,
            "source": source,
            "source_url": source_url,
            "collected_at": start.isoformat(),
            "run_id": run_id,
            "http_status": http_status,
            "status": status,
            "note": note,
            "raw": raw,
        }
        if extra:
            rec["extra"] = extra
        records.append(rec)

    # Hacker News
    hn_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    r = fetch(hn_url)
    hn_ids: list = []
    hn_http = r["http_status"]
    if r["ok"] and r["http_status"] == 200:
        try:
            hn_ids = json.loads(r["body"].decode("utf-8"))
            add("hacker_news", hn_url, "hn:topstories", hn_ids, r["http_status"], "OK", f"count={len(hn_ids)}", {"elapsed_s": r["elapsed_s"]})
        except Exception as e:  # noqa: BLE001
            add("hacker_news", hn_url, "hn:topstories", None, r["http_status"], "FAILED", f"json parse: {e}")
            hn_ids = []
    else:
        add("hacker_news", hn_url, "hn:topstories", r["body"][:500].decode("utf-8", "replace") if r["body"] else None, r["http_status"], "FAILED", r["error"] or "non-200")

    for sid in (hn_ids[: args.hn_items] if isinstance(hn_ids, list) else []):
        item_url = f"https://hacker-news.firebaseio.com/v0/item/{sid}.json"
        page_url = f"https://news.ycombinator.com/item?id={sid}"
        ir = fetch(item_url)
        if ir["ok"] and ir["http_status"] == 200:
            try:
                raw = json.loads(ir["body"].decode("utf-8"))
                add("hacker_news", page_url, f"hn:item:{sid}", raw, ir["http_status"], "OK", "item", {"api_url": item_url})
            except Exception as e:  # noqa: BLE001
                add("hacker_news", item_url, f"hn:item:{sid}", None, ir["http_status"], "FAILED", f"json parse: {e}")
        else:
            add("hacker_news", item_url, f"hn:item:{sid}", None, ir["http_status"], "FAILED", ir["error"] or "non-200")

    # Hugging Face
    hf_url = f"https://huggingface.co/api/models?limit={args.hf_limit}"
    hf = fetch(hf_url)
    hf_http = hf["http_status"]
    if hf["ok"] and hf["http_status"] == 200:
        try:
            models = json.loads(hf["body"].decode("utf-8"))
            if not models:
                add("huggingface", hf_url, "hf:models:empty", models, hf["http_status"], "EMPTY", "HTTP 200 empty list", {"etag": hf["headers"].get("etag")})
            else:
                for m in models:
                    mid = m.get("id") or m.get("_id") or "unknown"
                    page = f"https://huggingface.co/{mid}" if m.get("id") else hf_url
                    add("huggingface", page, f"hf:model:{mid}", m, hf["http_status"], "OK", "model", {"etag": hf["headers"].get("etag")})
        except Exception as e:  # noqa: BLE001
            add("huggingface", hf_url, "hf:models", None, hf["http_status"], "FAILED", f"json parse: {e}")
    else:
        add("huggingface", hf_url, "hf:models", hf["body"][:500].decode("utf-8", "replace") if hf["body"] else None, hf["http_status"], "FAILED", hf["error"] or "non-200")

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
    # one ledger row per source group
    append_ledger(ledger_path, {
        "run_id": run_id,
        "planned_at": planned_column(args.planned_at),
        "actual_start": start.isoformat(),
        "actual_end": end.isoformat(),
        "source": "hacker_news+huggingface",
        "http_status": f"hn={hn_http};hf={hf_http}",
        "result_count": len(records),
        "new_count": new_count,
        "dup_count": dup_count,
        "failed_count": failed_count,
        "cost_usd": args.cost_usd,
        "notes": ("script-only; no model inference" + (f"; trigger={args.trigger}" if args.trigger else "") + ("; planned_at=timestamp" if planned_column(args.planned_at) else "; planned_at=blank")),
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
    }
    (out / f"summary_{run_id}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
