# Hands-off automation — publish even when your PC is off

Two independent layers. Use either or both.

## Layer 1 — GitHub Actions (recommended: fully PC-independent)

The pipeline runs on GitHub's servers on a daily schedule. Your computer can be
off, asleep, or offline. Free (Actions minutes are unlimited for public repos,
2000 min/month for private; a run is ~8 min).

**One-time setup**

1. Push this repo to GitHub (you already have `github.com/kjuhoho/political-shorts`):
   ```
   git add -A && git commit -m "automation" && git push
   ```
2. Turn your local config + YouTube credentials into repo secrets:
   ```
   python scripts/pack_secrets.py            # prints 3 `gh secret set ...` lines
   ```
   Run those 3 lines (needs the `gh` CLI, logged in), **or** open
   GitHub → repo → *Settings → Secrets and variables → Actions → New repository
   secret* and paste each value by hand. The three secrets:
   | name | contents |
   |---|---|
   | `PS_ENV_B64` | base64 of your `.env` |
   | `PS_YT_CLIENT_B64` | base64 of `secrets/client_secret_youtube.json` |
   | `PS_YT_TOKEN_B64` | base64 of `secrets/token_youtube.json` |
3. GitHub → repo → *Actions* tab → enable workflows if prompted.

**That's it.** `.github/workflows/daily-short.yml` then runs at **07:00 and
17:00 KST** every day: collect → pick the hottest non-duplicate story → build →
publish to YouTube. It commits the dedup DB (`data/political_shorts.sqlite3`)
back to the repo after each run so it never repeats a video, and uploads the
`.mp4` as a downloadable artifact (kept 7 days).

Run it by hand any time: *Actions → daily political short → Run workflow*
(toggle "publish" off for a dry run).

**Notes**
- The refresh token is durable (the OAuth app is in Production), so the run
  keeps working without you re-authing.
- If `edge-tts` ever can't reach Microsoft from CI the video still builds, just
  with caption-only (silent) cards — check the artifact.

## Layer 2 — pre-scheduled uploads (works while the PC is on now, publishes later)

Build a video now and let **YouTube itself** flip it public at a future time —
survives the PC being off at that moment:

```
python scripts/publish_one.py output/<file>.mp4 --publish-at 2026-09-04T07:00
```

Good for buffering a few days ahead before a trip. Pair it with
`scripts/check_topic.py "<headline>"` first so you don't schedule a duplicate.

## Local Windows tasks (Layer 0 — least reliable)

`PoliticalShortsMorning` (07:00) and `PoliticalShortsEvening` (17:00) still
exist but only run if the PC is on **and** online at that minute — they missed
twice. `PoliticalShortsMorning` is currently **disabled**; re-enable it in Task
Scheduler only if you're not using Layer 1.
