# data

Public collection outputs only: raw records, source URLs, timestamps, summaries, dedup flags, the ledger, and feed freshness.

`planned_at` is an Asia/Shanghai ISO timestamp for a schedule slot, or blank for a manual run. It is never the cron expression.

`schedule_status.json` counts successful `schedule` runs only. `workflow_dispatch` does not count.

Do not place store data, Qwen internal data, credentials, cookies, or private diagnostics here.
