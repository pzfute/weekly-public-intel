# weekly-public-intel

Public collector for first-hand AI builder posts and long-tail product discussion candidates.

No paid APIs and no model calls. `cost_usd` stays 0. This repo does not run store, Qwen, Dianxiaomi, or other private workflows.

## Sources

| Script | What it collects |
| --- | --- |
| `collector/collect_public_intel.py` | Hacker News public API and Hugging Face public model API |
| `collector/collect_longtail.py` | Stack Exchange discussions on diy, mechanics, and hardwarerecs |
| `collector/collect_github_releases.py` | Public releases for `modelcontextprotocol/servers`, `huggingface/transformers`, and `cli/cli` |
| `collector/collect_builder_feeds.py` | Pinned public JSON from [follow-builders](https://github.com/zarazhangrui/follow-builders) |

Long-tail rows are `候选`. A public question is discussion evidence. It is not search volume, a live listing, a completed order, or demand verification. eBay is not enabled.

### follow-builders pin

Reviewed commit [`ed6f7d5b4b032923d44ca428d1213127366b6932`](https://github.com/zarazhangrui/follow-builders/commit/ed6f7d5b4b032923d44ca428d1213127366b6932) (2026-11-21T06:27:19Z). The collector downloads only these public JSON blobs and checks the git blob sha:

- `feed-x.json`
- `feed-podcasts.json`
- `feed-blogs.json`
- `config/default-sources.json`

Upstream `SKILL.md`, `scripts/`, and `prompts/` are not installed or executed. Podcast transcripts are not copied into this repo. The source list at that commit has 26 X accounts, 6 podcasts, and 2 blogs. A snapshot that contains fewer accounts is incomplete coverage, not 26 fresh timelines. `data/feed_freshness.json` records snapshot age, missing accounts, and whether the snapshot is older than 24 hours.

The 26 X accounts are: Andrej Karpathy, Swyx, Josh Woodward, Boris Cherny, Thibault Sottiaux, Peter Yang, Nan Yu, Madhu Guru, Amanda Askell, Cat Wu, Thariq, Google Labs, Amjad Masad, Guillermo Rauch, Alex Albert, Aaron Levie, Ryo Lu, Garry Tan, Matt Turck, Zara Zhang, Nikunj Kothari, Peter Steinberger, Dan Shipper, Aditya Agarwal, Sam Altman, Claude.

## Schedule

GitHub's workflow syntax, read on 2026-11-22, says `on.schedule` cron is UTC unless the same list item sets `timezone` to an IANA name. `github.event.schedule` is that cron string. This repo converts the firing cron into an Asia/Shanghai timestamp and stores that in `planned_at`. A manual run leaves `planned_at` blank.

Schedules run only on the default branch. They can be delayed, and a high-load queue can drop a run. Public scheduled workflows stop after 60 days without repository activity. Minute 30 is used because the docs call out the start of each hour as a high-load window.

On 2026-11-22 at 12:38 Asia/Shanghai, 10:30 that day had already passed. The acceptance schedule is the next three dates, all at 10:30 Asia/Shanghai (02:30 UTC):

- 2026-11-23
- 2026-11-24
- 2026-11-25

`workflow_dispatch` is for a real manual check and does not count. After three successful schedule runs on those dates, the workflow file is switched to Sunday 10:30 Asia/Shanghai:

```yaml
- cron: "30 10 * * 0"
  timezone: "Asia/Shanghai"
```

Until that third schedule success, Sunday is not active. The next Sunday after the acceptance window is 2026-11-29 10:30 Asia/Shanghai, and it runs only after the switch has landed.

## Output

- `data/records.jsonl` — source URL, time, raw public payload, summary, dedup flag
- `data/ledger.csv` — planned timestamp, actual start/end, HTTP status, new/dup/failed counts
- `data/summary_<run_id>.json`
- `data/feed_freshness.json` — snapshot age and missing accounts
- `data/schedule_acceptance.jsonl` and `data/schedule_status.json`
