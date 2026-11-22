#!/usr/bin/env python3
"""Record schedule acceptance. Manual runs never count.

After three successful schedule runs on different Asia/Shanghai dates,
replace the acceptance cron with Sunday 10:30 Asia/Shanghai.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
REQUIRED = 3
ACCEPTANCE_BLOCK = '''    # SCHEDULE_MODE: acceptance
    - cron: "30 10 23,24,25 11 *"
      timezone: "Asia/Shanghai"'''
WEEKLY_BLOCK = '''    # SCHEDULE_MODE: weekly
    - cron: "30 10 * * 0"
      timezone: "Asia/Shanghai"'''


def now_cn() -> dt.datetime:
    return dt.datetime.now(TZ)


def date_key(planned_at: str) -> str:
    if not planned_at or "T" not in planned_at:
        return ""
    try:
        stamp = dt.datetime.fromisoformat(planned_at)
    except ValueError:
        return ""
    return stamp.astimezone(TZ).date().isoformat()


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def successful_dates(rows: list[dict]) -> list[str]:
    found: list[str] = []
    for row in rows:
        if not row.get("counts_as_schedule_success"):
            continue
        key = date_key(str(row.get("planned_at") or ""))
        if key and key not in found:
            found.append(key)
    return found


def maybe_switch(workflow: Path, count: int) -> str:
    if not workflow.exists():
        return "workflow_missing"
    text = workflow.read_text(encoding="utf-8")
    if WEEKLY_BLOCK in text and ACCEPTANCE_BLOCK not in text:
        return "already_weekly"
    if count < REQUIRED:
        return "still_acceptance"
    if ACCEPTANCE_BLOCK not in text:
        return "acceptance_block_missing"
    workflow.write_text(text.replace(ACCEPTANCE_BLOCK, WEEKLY_BLOCK, 1), encoding="utf-8")
    return "switched_to_weekly"


def write_status(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workflow = root / "public-intel.yml"
        workflow.write_text("on:\n  schedule:\n" + ACCEPTANCE_BLOCK + "\n", encoding="utf-8")
        out = root / "data"
        out.mkdir()
        assert date_key("30 10 23,24,25 11 *") == ""
        for planned, run in (
            ("2026-11-23T10:30:00+08:00", "a"),
            ("2026-11-24T10:30:00+08:00", "b"),
        ):
            main_args = argparse.Namespace(
                out=str(out),
                trigger="schedule",
                planned_at=planned,
                status="ok",
                run_id=run,
                github_run_id=run,
                workflow=str(workflow),
                now="2026-11-23T10:40:00+08:00",
                run_url="",
            )
            assert run_once(main_args) == 0
        assert "SCHEDULE_MODE: acceptance" in workflow.read_text(encoding="utf-8")
        manual = argparse.Namespace(
            out=str(out),
            trigger="workflow_dispatch",
            planned_at="",
            status="ok",
            run_id="manual",
            github_run_id="m",
            workflow=str(workflow),
            now="2026-11-23T11:00:00+08:00",
            run_url="",
        )
        assert run_once(manual) == 0
        status = json.loads((out / "schedule_status.json").read_text(encoding="utf-8"))
        assert status["successful_schedule_count"] == 2, status
        third = argparse.Namespace(
            out=str(out),
            trigger="schedule",
            planned_at="2026-11-25T10:30:00+08:00",
            status="ok",
            run_id="c",
            github_run_id="c",
            workflow=str(workflow),
            now="2026-11-25T10:40:00+08:00",
            run_url="",
        )
        assert run_once(third) == 0
        text = workflow.read_text(encoding="utf-8")
        assert "SCHEDULE_MODE: weekly" in text
        assert 'cron: "30 10 * * 0"' in text
        assert "23,24,25" not in text
        print("record_schedule_run self-test ok")


def run_once(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "schedule_acceptance.jsonl"
    rows = load_rows(log_path)
    planned = args.planned_at.strip()
    counts = args.trigger == "schedule" and args.status == "ok" and bool(date_key(planned))
    recorded_at = args.now or now_cn().isoformat()
    row = {
        "recorded_at": recorded_at,
        "run_id": args.run_id,
        "github_run_id": args.github_run_id,
        "run_url": args.run_url,
        "trigger": args.trigger,
        "planned_at": planned,
        "planned_date_cst": date_key(planned),
        "status": args.status,
        "counts_as_schedule_success": counts,
    }
    rows.append(row)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    dates = successful_dates(rows)
    switch = maybe_switch(Path(args.workflow), len(dates)) if args.trigger == "schedule" and args.status == "ok" else "not_evaluated"
    payload = {
        "timezone": "Asia/Shanghai",
        "window_local": "10:00-20:00",
        "acceptance_time_cst": "10:30",
        "acceptance_dates_cst": ["2026-11-23", "2026-11-24", "2026-11-25"],
        "required_schedule_successes": REQUIRED,
        "successful_schedule_dates": dates,
        "successful_schedule_count": len(dates),
        "manual_counts": False,
        "last_trigger": args.trigger,
        "last_status": args.status,
        "last_counts_as_schedule_success": counts,
        "weekly_cron": "30 10 * * 0",
        "weekly_timezone": "Asia/Shanghai",
        "weekly_active": switch in {"switched_to_weekly", "already_weekly"},
        "switch_result": switch,
        "next_schedule_cst": _next_pending(dates, switch),
        "updated_at": recorded_at,
    }
    write_status(out / "schedule_status.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _next_pending(dates: list[str], switch: str) -> str:
    if switch in {"switched_to_weekly", "already_weekly"}:
        return "Sunday 10:30 Asia/Shanghai (30 10 * * 0)"
    pending = [d for d in ("2026-11-23", "2026-11-24", "2026-11-25") if d not in dates]
    if not pending:
        return "acceptance dates exhausted; weekly switch did not complete"
    return f"{pending[0]}T10:30:00+08:00"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data")
    parser.add_argument("--trigger", default="")
    parser.add_argument("--planned-at", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--github-run-id", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--workflow", default=".github/workflows/public-intel.yml")
    parser.add_argument("--now", default="")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.trigger or not args.status:
        parser.error("--trigger and --status are required")
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
