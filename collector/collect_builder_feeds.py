#!/usr/bin/env python3
"""Read pinned public follow-builders JSON feeds. No upstream scripts or prompts.

The feed is a snapshot, not a live pull of all configured accounts. Missing
accounts stay missing. Full podcast transcripts are not stored.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from ledger import planned_column

TZ = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "public-intel-collect/1.0"}
STALE_AFTER_HOURS = 24
PIN_PATH = Path(__file__).with_name("follow_builders_pin.json")
# IDs checked against the 2026-11-21 manual evidence. The absent id was an
# out-of-band read and must not be invented when this pinned feed lacks it.
AUDIT_PRESENT = (
    "2123943606906138868",
    "2123879309463228445",
    "2123869175085191298",
)
AUDIT_ABSENT = ("2124322968499282388",)


def now_cn() -> dt.datetime:
    return dt.datetime.now(TZ)


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def fetch(url: str, timeout: int = 30) -> dict:
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
            "error": f"HTTPError {e.code}",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "http_status": None,
            "url": url,
            "elapsed_s": round(time.time() - t0, 3),
            "body": b"",
            "error": f"{type(e).__name__}: {e}",
        }


def load_seen(path: Path) -> set[str]:
    seen: set[str] = set()
    if not path.exists():
        return seen
    with path.open(encoding="utf-8") as fh:
        for line in fh:
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
    import csv
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fields})


def parse_time(value: str | None) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def excerpt(text: str, limit: int = 240) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def age_hours(stamp: dt.datetime | None, start: dt.datetime) -> float | None:
    if stamp is None:
        return None
    return round((start.astimezone(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc)).total_seconds() / 3600, 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="out")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--planned-at", default="")
    parser.add_argument("--cost-usd", default="0")
    parser.add_argument("--trigger", default="")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records_path = out / "records.jsonl"
    ledger_path = out / "ledger.csv"
    start = now_cn()
    run_id = args.run_id or start.strftime("run_%Y%m%d_%H%M%S")
    seen = load_seen(records_path)
    records: list[dict] = []
    http_notes: list[str] = []
    pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    commit = pin["commit"]
    base = f"https://raw.githubusercontent.com/zarazhangrui/follow-builders/{commit}"

    def add(uid, source_url, http_status, status, note, raw, extra=None):
        rec = {
            "id": uid,
            "source": "follow_builders_feed",
            "source_url": source_url,
            "collected_at": start.isoformat(),
            "run_id": run_id,
            "http_status": http_status,
            "status": status,
            "note": note,
            "summary": excerpt(note if status != "OK" else (extra or {}).get("summary_text") or note),
            "raw": raw,
        }
        if extra:
            rec["extra"] = extra
            if extra.get("summary_text"):
                rec["summary"] = excerpt(extra["summary_text"])
        records.append(rec)

    def load_pinned(rel: str):
        url = f"{base}/{rel}"
        fetched = fetch(url)
        http_notes.append(f"{rel}={fetched['http_status']}")
        expected = pin["files"][rel]
        if not (fetched["ok"] and fetched["http_status"] == 200):
            add(
                f"follow-builders:{rel}:fetch",
                url,
                fetched["http_status"],
                "FAILED",
                fetched["error"] or "non-200",
                (fetched["body"][:300].decode("utf-8", "replace") if fetched["body"] else None),
            )
            return None
        actual = git_blob_sha1(fetched["body"])
        if actual != expected:
            add(
                f"follow-builders:{rel}:sha",
                url,
                fetched["http_status"],
                "FAILED",
                f"blob sha {actual} != pinned {expected}",
                None,
            )
            return None
        try:
            return json.loads(fetched["body"].decode("utf-8")), fetched
        except Exception as exc:  # noqa: BLE001
            add(f"follow-builders:{rel}:json", url, fetched["http_status"], "FAILED", f"json parse: {exc}", None)
            return None

    loaded = {}
    for rel in ("config/default-sources.json", "feed-x.json", "feed-podcasts.json", "feed-blogs.json"):
        parsed = load_pinned(rel)
        loaded[rel] = parsed

    sources_doc = loaded["config/default-sources.json"][0] if loaded["config/default-sources.json"] else None
    accounts = []
    podcasts_cfg = []
    blogs_cfg = []
    if isinstance(sources_doc, dict):
        accounts = sources_doc.get("x_accounts") or []
        podcasts_cfg = sources_doc.get("podcasts") or []
        blogs_cfg = sources_doc.get("blogs") or []
        if len(accounts) != 26:
            add(
                "follow-builders:x_accounts:count",
                f"{base}/config/default-sources.json",
                200,
                "FAILED",
                f"expected 26 reviewed accounts, found {len(accounts)}",
                None,
            )

    freshness = {
        "pin_commit": commit,
        "pin_repo": pin["repo"],
        "collected_at": start.isoformat(),
        "stale_after_hours": STALE_AFTER_HOURS,
        "configured_x_accounts": len(accounts),
        "configured_podcasts": len(podcasts_cfg),
        "configured_blogs": len(blogs_cfg),
        "feeds": {},
        "audit_crosscheck": {},
        "note": "Configured accounts are not the same thing as accounts present in this snapshot.",
    }

    x_doc = loaded["feed-x.json"][0] if loaded["feed-x.json"] else None
    present_handles = set()
    seen_ids = set()
    if isinstance(x_doc, dict):
        generated = parse_time(x_doc.get("generatedAt"))
        gen_age = age_hours(generated, start)
        stale = gen_age is None or gen_age > STALE_AFTER_HOURS
        builders = x_doc.get("x") or []
        tweets = []
        for builder in builders:
            handle = builder.get("handle") or ""
            present_handles.add(handle.lower())
            for tw in builder.get("tweets") or []:
                tid = str(tw.get("id") or "")
                url = tw.get("url") or ""
                if not tid or not url:
                    add(
                        f"x:invalid:{handle}:{tid or 'missing'}",
                        url or f"https://x.com/{handle}",
                        200,
                        "FAILED",
                        "tweet missing id or url",
                        {"handle": handle},
                    )
                    continue
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                created = parse_time(tw.get("createdAt"))
                text = tw.get("text") or ""
                item_age = age_hours(created, start)
                summary_text = f"{builder.get('name') or handle} @{handle} {tw.get('createdAt')}: {excerpt(text)}"
                tweets.append(tw.get("createdAt"))
                add(
                    f"x:{tid}",
                    url,
                    200,
                    "OK",
                    "public post from pinned feed snapshot",
                    {
                        "id": tid,
                        "handle": handle,
                        "name": builder.get("name"),
                        "createdAt": tw.get("createdAt"),
                        "url": url,
                        "text": text,
                        "feed_generated_at": x_doc.get("generatedAt"),
                    },
                    {
                        "summary_text": summary_text,
                        "feed_generated_at": x_doc.get("generatedAt"),
                        "snapshot_age_hours": gen_age,
                        "snapshot_stale": stale,
                        "item_age_hours": item_age,
                        "handle": handle,
                    },
                )
        missing = [a for a in accounts if str(a.get("handle") or "").lower() not in present_handles]
        freshness["feeds"]["x"] = {
            "generated_at": x_doc.get("generatedAt"),
            "lookback_hours": x_doc.get("lookbackHours"),
            "snapshot_age_hours": gen_age,
            "stale": stale,
            "builders_in_snapshot": len(builders),
            "posts": len(seen_ids),
            "missing_accounts": [
                {"name": a.get("name"), "handle": a.get("handle"), "url": f"https://x.com/{a.get('handle')}"}
                for a in missing
            ],
            "upstream_stats": x_doc.get("stats"),
        }
        found = {i: False for i in AUDIT_PRESENT}
        for tid in AUDIT_PRESENT:
            found[tid] = f"x:{tid}" in {r["id"] for r in records}
        absent_ok = {tid: all(r["id"] != f"x:{tid}" for r in records) for tid in AUDIT_ABSENT}
        freshness["audit_crosscheck"] = {
            "expected_in_pinned_feed": found,
            "expected_absent_not_invented": absent_ok,
        }
        if not all(found.values()) or not all(absent_ok.values()):
            add(
                "follow-builders:audit-crosscheck",
                f"{base}/feed-x.json",
                200,
                "FAILED",
                "pinned feed did not match the reviewed manual evidence ids",
                freshness["audit_crosscheck"],
            )
    elif loaded["feed-x.json"] is None:
        freshness["feeds"]["x"] = {"stale": True, "error": "fetch_or_pin_failed"}

    pod_doc = loaded["feed-podcasts.json"][0] if loaded["feed-podcasts.json"] else None
    if isinstance(pod_doc, dict):
        generated = parse_time(pod_doc.get("generatedAt"))
        gen_age = age_hours(generated, start)
        stale = gen_age is None or gen_age > STALE_AFTER_HOURS
        episodes = pod_doc.get("podcasts") or []
        freshness["feeds"]["podcasts"] = {
            "generated_at": pod_doc.get("generatedAt"),
            "lookback_hours": pod_doc.get("lookbackHours"),
            "snapshot_age_hours": gen_age,
            "stale": stale,
            "episodes": len(episodes),
            "upstream_stats": pod_doc.get("stats"),
            "transcript_stored": False,
        }
        if not episodes:
            add(
                "podcast:snapshot:empty",
                f"{base}/feed-podcasts.json",
                200,
                "EMPTY",
                "pinned podcast snapshot has no episodes",
                {"generatedAt": pod_doc.get("generatedAt")},
            )
        for ep in episodes:
            guid = ep.get("guid") or ep.get("url") or "unknown"
            transcript = ep.get("transcript") or ""
            published = ep.get("publishedAt")
            summary_text = f"{ep.get('name')} — {ep.get('title')} ({published})"
            add(
                f"podcast:{guid}",
                ep.get("url") or f"{base}/feed-podcasts.json",
                200,
                "OK",
                "metadata only; transcript not stored",
                {
                    "name": ep.get("name"),
                    "title": ep.get("title"),
                    "guid": ep.get("guid"),
                    "url": ep.get("url"),
                    "publishedAt": published,
                    "transcript_chars": len(transcript),
                    "transcript_stored": False,
                    "feed_generated_at": pod_doc.get("generatedAt"),
                },
                {
                    "summary_text": summary_text,
                    "snapshot_age_hours": gen_age,
                    "snapshot_stale": stale,
                    "item_age_hours": age_hours(parse_time(published), start),
                },
            )
    elif loaded["feed-podcasts.json"] is None:
        freshness["feeds"]["podcasts"] = {"stale": True, "error": "fetch_or_pin_failed"}

    blog_doc = loaded["feed-blogs.json"][0] if loaded["feed-blogs.json"] else None
    if isinstance(blog_doc, dict):
        generated = parse_time(blog_doc.get("generatedAt"))
        gen_age = age_hours(generated, start)
        stale = gen_age is None or gen_age > STALE_AFTER_HOURS
        posts = blog_doc.get("blogs") or []
        freshness["feeds"]["blogs"] = {
            "generated_at": blog_doc.get("generatedAt"),
            "lookback_hours": blog_doc.get("lookbackHours"),
            "snapshot_age_hours": gen_age,
            "stale": stale,
            "posts": len(posts),
            "upstream_stats": blog_doc.get("stats"),
        }
        if not posts:
            add(
                "blog:snapshot:empty",
                f"{base}/feed-blogs.json",
                200,
                "EMPTY",
                "pinned blog snapshot is empty",
                {"generatedAt": blog_doc.get("generatedAt"), "configured": [b.get("name") for b in blogs_cfg]},
            )
        for post in posts:
            url = post.get("url") or f"{base}/feed-blogs.json"
            pid = post.get("id") or post.get("guid") or url
            summary_text = f"{post.get('name') or post.get('source')}: {post.get('title') or url}"
            add(
                f"blog:{pid}",
                url,
                200,
                "OK",
                "public blog item from pinned feed",
                {
                    "title": post.get("title"),
                    "url": url,
                    "publishedAt": post.get("publishedAt") or post.get("published_at"),
                    "feed_generated_at": blog_doc.get("generatedAt"),
                },
                {"summary_text": summary_text, "snapshot_stale": stale, "snapshot_age_hours": gen_age},
            )
    elif loaded["feed-blogs.json"] is None:
        freshness["feeds"]["blogs"] = {"stale": True, "error": "fetch_or_pin_failed"}

    any_stale = any(bool((freshness["feeds"].get(name) or {}).get("stale")) for name in ("x", "podcasts", "blogs"))
    freshness["any_snapshot_stale"] = any_stale
    (out / "feed_freshness.json").write_text(json.dumps(freshness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    new_count = dup_count = failed_count = 0
    with records_path.open("a", encoding="utf-8") as fh:
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
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    end = now_cn()
    planned = planned_column(args.planned_at)
    notes = "pinned public JSON only; upstream scripts not executed; transcripts not stored"
    if args.trigger:
        notes += f"; trigger={args.trigger}"
    notes += "; planned_at=timestamp" if planned else "; planned_at=blank"
    notes += f"; snapshot_stale={any_stale}; configured_x={len(accounts)}; posts={len(seen_ids)}"
    append_ledger(ledger_path, {
        "run_id": run_id,
        "planned_at": planned,
        "actual_start": start.isoformat(),
        "actual_end": end.isoformat(),
        "source": "follow_builders_feed",
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
        "planned_at": planned,
        "planned_at_kind": "schedule_timestamp" if planned else "not_scheduled",
        "actual_start": start.isoformat(),
        "actual_end": end.isoformat(),
        "result_count": len(records),
        "new_count": new_count,
        "dup_count": dup_count,
        "failed_count": failed_count,
        "cost_usd": args.cost_usd,
        "model_inference": 0,
        "paid_api_calls": 0,
        "snapshot_stale": any_stale,
        "digest": [
            {"id": rec["id"], "source_url": rec["source_url"], "summary": rec.get("summary"), "status": rec["status"]}
            for rec in records
            if rec["status"] in {"OK", "EMPTY", "FAILED"}
        ],
    }
    (out / f"summary_{run_id}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in summary if k != "digest"}, ensure_ascii=False, indent=2))
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
