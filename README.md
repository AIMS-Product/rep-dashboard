[README.md](https://github.com/user-attachments/files/25529906/README.md)
# Sales Dashboard (GitHub Pages)

Automated MTD sales dashboard that pulls Closed/Won opportunity data from Close CRM and displays it on a GitHub Pages site.

## What It Shows

- **MTD Revenue** — total value of Closed/Won opportunities this month
- **MTD Deals Closed** — count of unique Closed/Won deals this month
- **Per-Rep Breakdown** — deals, revenue, and % to quota for each rep

## Quick Setup (5 minutes)

### 1. Create the GitHub repo

Create a **new repository** on GitHub (public or private with GitHub Pro).
Push this project:

```bash
git init
git add .
git commit -m "Initial dashboard setup"
git branch -M main
git remote add origin https://github.com/YOUR_ORG/sales-dashboard.git
git push -u origin main
```

### 2. Add the Close API key as a secret

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `CLOSE_API_KEY`
4. Value: your Close API key
5. Click **Add secret**

> ⚠️ **Never commit the API key to the repo.** The GitHub Actions workflow reads it from the secret at runtime.

### 3. Enable GitHub Pages

1. Go to your repo → **Settings** → **Pages**
2. Under **Source**, select **Deploy from a branch**
3. Branch: `main`, Folder: `/ (root)`
4. Click **Save**

Your dashboard will be live at: `https://YOUR_ORG.github.io/sales-dashboard/`

### 4. Trigger the first data pull

1. Go to the repo → **Actions** tab
2. Click **Update Sales Dashboard** in the left sidebar
3. Click **Run workflow** → **Run workflow**
4. Wait ~30 seconds for it to complete
5. Visit your GitHub Pages URL — you should see live data

## How It Works

```
GitHub Actions (hourly cron)
    ↓
scripts/fetch_data.py (Python)
    ↓  Fetches from Close API:
    ↓  GET /opportunity/?status_id=Closed/Won&date_won__gte=YYYY-MM-01
    ↓
data.json (committed to repo)
    ↓
index.html (reads data.json, renders dashboard)
    ↓
GitHub Pages (serves index.html)
```

**Schedule**: Runs every hour Mon–Fri, 7 AM – 5 PM PST. Also runs on manual trigger.

## Process Adherence

The production refresh adds aggregate process-adherence metrics to `data.json` before writing the
dashboard file and monthly archive. The adapter reads Close without modifying it and evaluates only
first calls scheduled in the dashboard month. The normal dashboard URL renders the adherence table.
The adherence cohort includes leads later marked Lost. Scores follow the rep assigned to the
first same-day meeting, with current Lead Owner used when Close has no identifiable meeting rep;
these attribution counts are included in `adherence_meta.cohort`.
Its scored-cohort count can therefore differ from the Booked / Shown board's count.

The Booked / Shown / Qualified board counts a lead as qualified when its Close `Qualified (Opp)`
field is `Yes`, using the same month, exclusions, and Lead Owner attribution as the per-rep booked
count. This matches the sales manager scorecard rule; qualification is not gated on the Show Up
field. Team funnel totals come from the published MTD Funnel dashboard and can differ from the sum
of rep rows. On Revenue Closed, Rev / Lead is each rep's month-to-date revenue divided by their
booked leads; AOV is month-to-date revenue divided by closed deals. A zero denominator displays
`N/A`. Team Rev / Lead is omitted because its booked denominator uses the separate MTD source.

Process Adherence has three pre-call and three post-call steps. All SMS evidence must come from the
current Lead Owner, including texts used for Loom evidence and post-call follow-up. Day-of
confirmation text requires an outbound SMS on the meeting's Pacific date before it begins. It may
also count for Pre-call text. Missing meeting or owner evidence leaves the confirmation step
unscored.

The post-call average is split into three separate requirements: Task created, FU meeting created,
and Recap email sent. A qualifying task must have a date, be assigned to the rep credited with the
first call, and be due at or after the call or created after it. A later non-canceled meeting for the
lead can be assigned to anyone and counts regardless of its outcome. Recap email sent accepts a sent
outbound email or a sent SMS from the current Lead Owner within 24 hours of the call. Post-call
eligibility does not depend on the First Call Show Up outcome. Closed / Won and Lost leads remain
visible, with all three post-call checks marked Exempt. This uses status at the time the local
preview is generated, so a later status or task change appears after the preview is regenerated.
Post-call uses the earliest live meeting on the booked date, or the booked-date fallback when none
exists; later meetings count only for FU meeting created.

The local adherence drawer has expandable Completed and Missed lead lists for each step. Neutral
leads appear in neither list. Lead names link to their Close records, and each row shows the scored
first-call time so later meetings on the same lead are not confused with the scored call. Names,
IDs, Close URLs, and booked and first-call dates are written to the ignored `data.preview.json` for
local review. Production `data.json` and monthly archives remain aggregate-only. Daily archives at
`archives/daily/` include scored lead names and Close links for the public GitHub Pages history; raw
activity and task records remain private and are not archived there. Daily files are finalized only
after the full 24-hour post-call window has elapsed.

For an isolated local verification artifact, run:

From this directory:

```bash
python3 scripts/build_adherence_preview.py
python3 -m http.server 8765 --bind 127.0.0.1
```

Then open:

```text
http://127.0.0.1:8765/index.html?preview=adherence
```

The ordinary dashboard URL loads production `data.json`; the query parameter loads the separately
generated `data.preview.json` artifact.

## Files

| File | Purpose |
|------|---------|
| `index.html` | Dashboard UI (loads `data.json` client-side) |
| `data.json` | Latest dashboard data (auto-updated by GitHub Actions) |
| `scripts/fetch_data.py` | Fetches Close metrics, adds adherence, then writes and archives `data.json` |
| `scripts/build_yesterday_preview.py` | Builds yesterday's local scorecard and archives matured daily lead-level results |
| `scripts/adherence_rules.py` | Pure, unit-tested adherence calculation rules |
| `scripts/build_adherence_preview.py` | Read-only adherence adapter used by production and the local preview |
| `tests/test_adherence_rules.py` | Boundary tests for eligibility, OR rules, timing, and averaging |
| `.github/workflows/update-dashboard.yml` | Hourly automation schedule |

## Customization

### Change rep quotas
Edit the `REP_QUOTAS` dict in `scripts/fetch_data.py`.

### Change schedule
Edit the `cron` lines in `.github/workflows/update-dashboard.yml`.

### Add/remove excluded users
Edit the `EXCLUDE_USERS` set in `scripts/fetch_data.py`.

## Troubleshooting

**Dashboard shows zeros**: Run the workflow manually (Actions → Run workflow) and check the logs.

**API errors**: Verify your `CLOSE_API_KEY` secret is set correctly. The key must have read access to opportunities and users.

**Page not loading**: Make sure GitHub Pages is enabled and pointing to the `main` branch root.

**Stale data**: The page auto-refreshes every 5 minutes. You can also hard-refresh (Ctrl+Shift+R).
