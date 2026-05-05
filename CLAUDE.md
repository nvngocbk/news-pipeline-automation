# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

Two Python pipelines that each produce a dated 1080x1920 MP4 "news bulletin" with Vietnamese voiceover, then upload the run directory to Google Drive via `rclone`. One pipeline covers Vietnamese domestic news, the other covers international news translated into Vietnamese. Everything downstream (filenames, run dirs, Drive paths, spoken time) is derived from runtime — nothing is hardcoded by date.

The authoritative operating rules live in [news/NEWS_PIPELINE_RULES.md](news/NEWS_PIPELINE_RULES.md). Read it before changing behavior; the rules below summarize the parts Claude most often needs.

## Entrypoints

Three top-level files are thin re-export shims — the real code is in [news/](news/):

- [run_vn_news_dynamic.py](run_vn_news_dynamic.py) → [news/scripts/run_vn_news_dynamic.py](news/scripts/run_vn_news_dynamic.py)
- [run_world_news_dynamic.py](run_world_news_dynamic.py) → [news/scripts/run_world_news_dynamic.py](news/scripts/run_world_news_dynamic.py)
- [news_runtime.py](news_runtime.py) → [news/core/runtime.py](news/core/runtime.py)

These are the **only** entrypoints cron is allowed to use (rule 2 in NEWS_PIPELINE_RULES.md). Any older snapshot/manual scripts belong in `legacy/`. There is no build/test/lint tooling; scripts are invoked directly:

```
python3 run_vn_news_dynamic.py
python3 run_world_news_dynamic.py
```

Running a single pipeline end-to-end performs network fetches, ffmpeg transcodes, Google TTS calls, and an rclone upload — it is not a cheap local test. To dry-run a specific window, set `RUN_DATE` / `RUN_HHMM` (see below).

## Runtime model

[news/core/runtime.py](news/core/runtime.py) centralizes time. All scripts call `get_runtime()` once and use its fields for filenames, run paths, voice opening ("bản tin cập nhật lúc {RUN_HOUR}"), and metadata. Override env vars (all optional):

- `NEWS_TIMEZONE` (default `Asia/Bangkok`)
- `RUN_DATE` (YYYY-MM-DD)
- `RUN_HHMM` (HHMM)
- `RUN_HOUR_24` (numeric, for scoring logic)
- `RUN_HOUR` (spoken form, e.g. `6 giờ` — VN pipeline reads this literally into the TTS script)

Never reintroduce a hardcoded date/hour anywhere in filenames, run dirs, voice script, metadata, or Drive paths.

## Editorial-selection controls

Both pipelines fetch RSS → normalize → filter → rank → pick N stories → download images → voice/video → upload. Env flags change selection:

- `FOCUS_KEYWORDS` — pipe-separated (`|`). When set, `focus_score` becomes the primary sort key and VN additionally reorders feeds to put politics/society first. It is also the only way to override the VN age cap — a focus-matching story older than `MAX_STORY_AGE_HOURS` can still be picked.
- `INCLUDE_YESTERDAY=1` — widens the anti-repeat / focus search to yesterday's runs.
- `MIN_FOCUS_MATCHES` — minimum focus-matching stories to seat before filling (World default 3; VN default `max(3, TARGET_STORIES // 2)`).
- VN-only: `TARGET_STORIES` (clamped to 8–10), `PRIOR_FILES_TO_SCAN` (default 50), `ROLLING_HOURS` (token-window, default 24h), `CLUSTER_ROLLING_HOURS` (cluster-signature window, default 168h), `MAX_STORY_AGE_HOURS` (hard age cap, default 36h), `HISTORY_MAX_LINES`.

Cron must not inherit focus from an ad-hoc manual test — leave these unset in the cron payload.

## Anti-repeat — note the two different storage shapes

The pipelines diverge here and it matters when debugging "why did this story repeat":

- **VN** reads/writes a single JSONL at `/home/nv-ngoc/.openclaw/workspace/news-vn-history.jsonl` and runs **two** anti-repeat windows in one walk:
  - **Token window** (`ROLLING_HOURS`, default 24h): compares tokens + `source_url` + categories; drops a candidate with ≥5-token overlap against priors when no `FOCUS_KEYWORDS` match.
  - **Cluster-signature window** (`CLUSTER_ROLLING_HOURS`, default 168h / 7 days): each story has `cluster_keys` of the form `<proper-noun>@<event>` (e.g. `vĩnh tuy@cháy`). A candidate whose any key hits a prior cluster is **hard-dropped** — no hot-score or recency bypass. This is the layer that stops day-N follow-up coverage of a week-old incident from resurfacing. Broad locations (`hà nội`, `tp hcm`, `đà nẵng`, …) are blocklisted from cluster keys so unrelated incidents in the same major city don't collide.
  - A hard age cap `MAX_STORY_AGE_HOURS` (default 36h) is applied **before** ranking in the main pass. Hot stories no longer bypass it; only focus-keyword matches do.
  - `PRIOR_FILES_TO_SCAN` (default 50) caps the outer history walk and must be ≥ expected runs-per-day × 7 for the cluster window to see its full range.
- **World** walks the prior `metadata.json` files under `/home/nv-ngoc/.openclaw/workspace/news-videos/{RUN_DATE}/`, optionally `{yesterday}/` when `INCLUDE_YESTERDAY=1`, scanning the last `PRIOR_FILES_TO_SCAN` runs. World does **not** use cluster signatures — it compares tokens only.

Anti-repeat compares **topic clusters**, not exact titles. When debugging a VN repeat, check both: `metadata.json` → `anti_repeat_note` prints both windows and prior-cluster count; the history JSONL contains `cluster_keys` per story (legacy rows without `cluster_keys` are derived on the fly).

## Pipeline invariants worth preserving

- Only the final selected stories should have images downloaded / ffmpeg-processed. Do not add "prepare everything in the pool then pick" flows — that was an observed regression (rule 8).
- Upload is success only when all four hold: ffmpeg render OK, `rclone copy` rc==0, `rclone lsf -R` rc==0, and `missing_remote_files == []`. The local `RUN_DIR` is deleted **only** inside this gate; otherwise it must be preserved for rerun (rules 10–11).
- The final JSON summary (printed to stdout + written to `news-video-last-summary*.json`) must reflect reality: no "uploaded + deleted" when Drive is actually missing files.
- Rule 9 voice style (VN): short opening, spoken hour as `6 giờ` / `19 giờ`, never `06 giờ 00`, no gratuitous date/year in the intro.

## Host assumptions

All absolute paths target the production mini-PC (`/home/nv-ngoc/...`), e.g. service account keys at `/home/nv-ngoc/keys/tts-sa.json` and the rclone remote `gdrive:OpenClaw Database/...`. These scripts will not run as-is on a dev laptop without either that filesystem layout or code changes. When editing, prefer keeping these paths centralized rather than sprinkling more literals.

## VN vs World — quick differences

| | VN ([run_vn_news_dynamic.py](news/scripts/run_vn_news_dynamic.py)) | World ([run_world_news_dynamic.py](news/scripts/run_world_news_dynamic.py)) |
|---|---|---|
| Feeds | VnExpress, Tuổi Trẻ, VietnamNet, Dân Trí, VietnamPlus | Reuters, AP, BBC, Guardian, DW, Al Jazeera, NYT |
| Story count | 8–10 (clamped) | 5 |
| Translation | none (content already vi) | Google translate REST → `vi`, prefixed with `Theo {source},` |
| TTS | `google-cloud-texttospeech` client, `vi-VN-Neural2-A`, per-story segments concatenated | REST call, fallback chain `Chirp3-HD-Aoede → Neural2-A → Wavenet-A`, single file |
| Run base | `/home/nv-ngoc/.openclaw/workspace/news-videos-vn/{date}/{hhmm}` | `/home/nv-ngoc/.openclaw/workspace/news-videos/{date}/{hhmm}` |
| History | JSONL rolling file | prior `metadata.json` per run |
| Image style | fit + blurred background (letterbox safe) | center-crop + lanczos + unsharp |

## One-off helper

[upload_to_drive.py](upload_to_drive.py) uploads a single file via the Google Drive API using a service account at `/home/nv-ngoc/keys/symbolic-pipe-491806-a8-ce6c0558fdce.json` into a hardcoded default folder. It is **not** part of the pipeline flow (pipelines use rclone); treat it as a manual tool.
