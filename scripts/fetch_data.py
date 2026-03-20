#!/usr/bin/env python3
"""
Fetch sales data from Close CRM and generate data.json for the GitHub Pages dashboard.

Data collected:
  1. Closed/Won opportunities (MTD + today) -> revenue & deal counts per rep
  2. Meeting activities classified by title (Option A) -> meetings booked per rep
  3. Lead-level "First Call Show Up (Opp)" = "Yes" -> meetings shown per rep
  4. Close rate = deals closed / meetings booked

Meeting methodology (Option A — matches Scorecard):
  - Paginates ALL meeting activities from Close API (~11,000+)
  - Converts starts_at UTC -> Pacific time, filters to current month through today
  - Classifies each title against inclusion/exclusion rules
  - Fetches leads for qualifying meetings to get Lead Owner, status, and show-up
  - Excludes leads in "Canceled (by Lead)" or "Outside the US" status
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, date, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from base64 import b64encode
from calendar import monthrange

# --- Configuration ---

CLOSE_API_KEY = os.environ.get("CLOSE_API_KEY", "")
BASE_URL = "https://api.close.com/api/v1"

PIPELINE_ID = "pipe_78hyBUVS7IKikGEmstObu1"
CLOSED_WON_STATUS_ID = "stat_WnFc0uhjcjV0cc3bVzdFVqDz7av6rbsOmOvHUsO6s03"

# Lead statuses to EXCLUDE from meeting counts (rep can't control these)
EXCLUDED_LEAD_STATUSES = {
    "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT",  # Canceled (by Lead)
    "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB",  # Outside the US
}

# Custom field IDs (lead object)
CF_FIRST_CALL_SHOW_ID   = "cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq"
CF_FIRST_CALL_SHOW_NAME = "First Call Show Up (Opp)"

CF_LEAD_OWNER_ID         = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
CF_LEAD_OWNER_NAME       = "Lead Owner"

TEAM_QUOTA = 906_000

REP_QUOTAS = {
    "Christian Hartwell": 50_000,
    "Lyle Hubbard": 100_000,
    "Ategeka Musinguzi": 100_000,
    "Scott Seymour": 100_000,
    "Eric Piccione": 100_000,
    "Jordan Humphrey": 100_000,
    "Jason Aaron": 100_000,
    "Robin Perkins": 100_000,
    "Ryan Jones": 100_000,
    "John Kirk": 100_000,
    "Jake Skinner": 75_000,
    "Vince Bartolini": 50_000,
    "Elvis Ellis": 50_000,
    "Chris Wanke": 50_000,
    "Andrea Shoop": 50_000,
}

# Fully excluded from all dashboard data (revenue, deals, meetings)
EXCLUDE_USERS = {"Mallory Kent", "Unknown", "Ahmad Bukhari", "Stephen Olivas", "Julia Scaroni"}

# Only appear on dashboard if they have closed deals that month
# Meeting data N/A'd out — only show on Revenue Closed and Opps Closed
DEALS_ONLY_USERS = {"Kristin Nelson", "Joe Dysert"}

# Revenue counts toward team totals but rep never appears as a row
REVENUE_ONLY_USERS = {"William Chase"}

# Managers: no quota, show "(mgr)" label, no "Ramping" badge
MANAGER_USERS = {"Joe Dysert"}

# Team leads: show "(lead)" label
LEAD_USERS = {"Christian Hartwell"}

# Users whose meeting activities are never counted (resolved to user_ids at runtime)
EXCLUDE_MEETING_USER_NAMES = {
    "Kristin Nelson", "Spencer Reynolds", "Stephen Olivas",
    "Ahmad Bukhari", "Mallory Kent", "Julia Scaroni", "William Chase", "Unknown",
}


# --- API helpers (with rate limiting + 429 retry) ---

_last_api_call = 0.0
API_THROTTLE = 0.5  # seconds between API calls


def api_get(endpoint, params=None):
    """Authenticated GET with rate limiting and 429 backoff."""
    global _last_api_call
    elapsed = time.time() - _last_api_call
    if elapsed < API_THROTTLE:
        time.sleep(API_THROTTLE - elapsed)

    url = f"{BASE_URL}{endpoint}"
    if params:
        url = f"{url}?{urlencode(params)}"

    auth = b64encode(f"{CLOSE_API_KEY}:".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            _last_api_call = time.time()
            req = Request(url, headers=headers)
            with urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  Rate limited (429), waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            body = e.read().decode() if e.fp else ""
            print(f"API error {e.code} for {url}: {body}", file=sys.stderr, flush=True)
            raise


def fetch_org_users():
    """Fetch all org users -> dict of user_id: full_name."""
    data = api_get("/user/")
    users = {}
    for u in data.get("data", []):
        first = u.get("first_name", "")
        last = u.get("last_name", "")
        full = f"{first} {last}".strip()
        users[u["id"]] = full
    return users


# --- Opportunity data (revenue + deals) ---

def fetch_closed_won_opportunities(year, month):
    """Fetch all Closed/Won opps in the given month from Sales Pipeline."""
    _, last_day = monthrange(year, month)
    date_gte = f"{year}-{month:02d}-01"
    date_lte = f"{year}-{month:02d}-{last_day:02d}"

    all_opps = []
    skip = 0
    limit = 100

    while True:
        params = {
            "status_id": CLOSED_WON_STATUS_ID,
            "date_won__gte": date_gte,
            "date_won__lte": date_lte,
            "_skip": str(skip),
            "_limit": str(limit),
        }
        data = api_get("/opportunity/", params)
        opps = data.get("data", [])
        all_opps.extend(opps)
        if not data.get("has_more", False):
            break
        skip += limit

    return [o for o in all_opps if o.get("pipeline_id") == PIPELINE_ID]


# --- Meeting title classification (matches Scorecard methodology) ---

_INCLUDED_TITLE_PATTERNS = [
    re.compile(r"vending\s+strategy\s+call", re.IGNORECASE),
    re.compile(r"vendingpren[eu]+rs?\s+consultation", re.IGNORECASE),
    re.compile(r"vendingpren[eu]+rs?\s+strategy\s+call", re.IGNORECASE),
    re.compile(r"new\s+vendingpren[eu]+r\s+strategy\s+call", re.IGNORECASE),
    re.compile(r"vending\s+consult\b", re.IGNORECASE),
]


def is_qualifying_meeting(title):
    """Classify a meeting title. Returns True if it's a qualifying first call.

    Rules applied in order — first match wins.
    Blank titles are excluded (not a known first-call pattern).
    """
    if not title or not title.strip():
        return False

    stripped = title.strip()

    # Rule 1: Canceled prefix
    if stripped.startswith("Canceled:"):
        return False

    t = stripped.lower()

    # Rule 2: Discovery calls (setter, not strategy)
    if "vending quick discovery" in t:
        return False

    # Rule 3: Follow-ups
    for p in ("follow-up", "follow up", "fallow up", "f/u", "next steps"):
        if p in t:
            return False

    # Rule 4: Rescheduled
    if "rescheduled" in t or "reschedule" in t:
        return False

    # Rule 5: Internal Q&A
    if "anthony" in t and "q&a" in t:
        return False

    # Rule 6: Enrollment / onboarding
    for p in ("enrollment", "silver start up", "bronze enrollment", "questions on enrollment"):
        if p in t:
            return False

    # Check inclusion patterns
    for pattern in _INCLUDED_TITLE_PATTERNS:
        if pattern.search(stripped):
            return True

    # Default: not a qualifying first call
    return False


# --- Meeting data (Option A: activity title classification) ---

def fetch_all_meeting_activities():
    """Paginate ALL meeting activities from Close API.

    Close silently ignores date filters on this endpoint,
    so we fetch everything and filter by date in Python.
    """
    all_meetings = []
    skip = 0
    limit = 100
    page = 0

    while True:
        page += 1
        params = {"_skip": str(skip), "_limit": str(limit)}
        data = api_get("/activity/meeting/", params)
        meetings = data.get("data", [])
        all_meetings.extend(meetings)

        if page % 25 == 0:
            print(f"    ... {len(all_meetings)} meetings ({page} pages)", flush=True)

        if not data.get("has_more", False):
            break
        skip += limit

    return all_meetings


def get_custom_value(custom_dict, field_id, field_name):
    """Try multiple key formats to get a custom field value from a lead."""
    val = custom_dict.get(field_name)
    if val is not None:
        return val
    val = custom_dict.get(field_id)
    if val is not None:
        return val
    val = custom_dict.get(f"custom.{field_id}")
    if val is not None:
        return val
    return ""


def resolve_owner_to_name(owner_raw, user_map, name_to_id):
    """Resolve a Lead Owner value (user_id, name, or dict) to a rep name."""
    if not owner_raw:
        return "Unknown"

    if isinstance(owner_raw, dict):
        uid = owner_raw.get("id", "")
        if uid in user_map:
            return user_map[uid]
        return owner_raw.get("name", "Unknown")

    owner_str = str(owner_raw).strip()
    if owner_str in user_map:
        return user_map[owner_str]
    if owner_str in name_to_id:
        return owner_str
    for rep_name in REP_QUOTAS:
        if owner_str == rep_name:
            return rep_name
    return owner_str if owner_str else "Unknown"


def fetch_meeting_data(year, month, today_str, pst_tz, user_map, name_to_id):
    """Fetch and classify meeting activities, then look up lead data.

    Uses Option A: meeting activity title classification (matches Scorecard).
    Returns (rep_booked, rep_shown) dicts.
    """
    # Build excluded user_ids from names
    exclude_uids = set()
    for name in EXCLUDE_MEETING_USER_NAMES:
        uid = name_to_id.get(name)
        if uid:
            exclude_uids.add(uid)
    print(f"  Excluding meetings from {len(exclude_uids)} user IDs", flush=True)

    # Step 1: Paginate all meetings
    print("  Paginating all meeting activities...", flush=True)
    all_meetings = fetch_all_meeting_activities()
    print(f"  Fetched {len(all_meetings)} total meetings.", flush=True)

    # Step 2: Filter by date + user + title
    date_start = f"{year}-{month:02d}-01"
    qualifying = []
    stats = {"title_excluded": 0, "user_excluded": 0, "date_excluded": 0}

    for m in all_meetings:
        if m.get("user_id") in exclude_uids:
            stats["user_excluded"] += 1
            continue

        starts_at = m.get("starts_at", "")
        if not starts_at:
            continue
        try:
            dt_utc = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
            dt_pst = dt_utc.astimezone(pst_tz)
            meeting_date = dt_pst.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        if meeting_date < date_start or meeting_date > today_str:
            stats["date_excluded"] += 1
            continue

        title = m.get("title", "") or ""
        if not is_qualifying_meeting(title):
            stats["title_excluded"] += 1
            continue

        qualifying.append({
            "lead_id": m.get("lead_id", ""),
            "date": meeting_date,
        })

    print(f"  Classification: {len(qualifying)} qualifying | "
          f"{stats['title_excluded']} title-excluded | "
          f"{stats['user_excluded']} user-excluded | "
          f"{stats['date_excluded']} outside date range", flush=True)

    # Step 3: Fetch unique leads
    unique_lead_ids = set(q["lead_id"] for q in qualifying if q["lead_id"])
    print(f"  Fetching {len(unique_lead_ids)} unique leads...", flush=True)

    lead_cache = {}
    fetched = 0
    for lid in unique_lead_ids:
        try:
            lead_cache[lid] = api_get(f"/lead/{lid}")
            fetched += 1
            if fetched % 25 == 0:
                print(f"    ... {fetched}/{len(unique_lead_ids)} leads", flush=True)
        except Exception as e:
            print(f"  Warning: could not fetch lead {lid}: {e}", flush=True)

    # Step 4: Attribute and count
    rep_booked = {}
    rep_shown = {}
    excluded_status = 0
    shown_leads = set()  # per-lead dedup for shown

    for q in qualifying:
        lid = q["lead_id"]
        lead = lead_cache.get(lid)
        if not lead:
            continue

        if lead.get("status_id", "") in EXCLUDED_LEAD_STATUSES:
            excluded_status += 1
            continue

        custom = lead.get("custom", {})
        merged = dict(custom)
        for k, v in lead.items():
            if k.startswith("custom."):
                merged[k] = v
                merged[k.replace("custom.", "")] = v

        owner_raw = get_custom_value(merged, CF_LEAD_OWNER_ID, CF_LEAD_OWNER_NAME)
        rep_name = resolve_owner_to_name(owner_raw, user_map, name_to_id)

        if rep_name in EXCLUDE_USERS:
            continue

        rep_booked[rep_name] = rep_booked.get(rep_name, 0) + 1

        # Shown: per unique lead per rep (avoid double-count)
        show_up = get_custom_value(merged, CF_FIRST_CALL_SHOW_ID, CF_FIRST_CALL_SHOW_NAME)
        shown_key = f"{rep_name}:{lid}"
        if str(show_up).strip().lower() == "yes" and shown_key not in shown_leads:
            rep_shown[rep_name] = rep_shown.get(rep_name, 0) + 1
            shown_leads.add(shown_key)

    print(f"  Excluded {excluded_status} meetings (Canceled/Outside US lead status)", flush=True)
    print(f"  Final: {sum(rep_booked.values())} booked, {sum(rep_shown.values())} shown", flush=True)

    return rep_booked, rep_shown


# --- Working days ---

def count_working_days(year, month, up_to_day=None):
    _, last_day = monthrange(year, month)
    end_day = min(up_to_day, last_day) if up_to_day else last_day
    count = 0
    for d in range(1, end_day + 1):
        if date(year, month, d).weekday() < 5:
            count += 1
    return count


# --- Main ---

def build_dashboard_data():
    if not CLOSE_API_KEY:
        print("ERROR: CLOSE_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        pst = ZoneInfo("America/Los_Angeles")
    except ImportError:
        pst = timezone(timedelta(hours=-8))
    now = now_utc.astimezone(pst)

    year, month, today_day = now.year, now.month, now.day
    today_str = now.strftime("%Y-%m-%d")
    _, last_day = monthrange(year, month)

    print(f"Fetching data for {year}-{month:02d} (day {today_day}, {now.strftime('%Z')})...", flush=True)

    # Step 1: User map
    print("  Fetching org users...", flush=True)
    user_map = fetch_org_users()
    name_to_id = {v: k for k, v in user_map.items()}
    print(f"  Found {len(user_map)} users.", flush=True)

    # Step 2: Closed/Won opportunities
    print("  Fetching Closed/Won opportunities...", flush=True)
    opps = fetch_closed_won_opportunities(year, month)
    print(f"  Found {len(opps)} Closed/Won opportunities.", flush=True)

    rep_revenue = {}
    rep_deals = {}
    today_revenue = 0.0
    today_deals = 0
    seen_leads = set()

    for opp in opps:
        user_id = opp.get("user_id")
        rep_name = user_map.get(user_id, "Unknown")
        if rep_name in EXCLUDE_USERS:
            continue

        value_dollars = (opp.get("value", 0) or 0) / 100
        lead_id = opp.get("lead_id", "")
        date_won = opp.get("date_won", "")

        rep_revenue[rep_name] = rep_revenue.get(rep_name, 0) + value_dollars

        lead_key = f"{rep_name}:{lead_id}"
        if lead_key not in seen_leads:
            rep_deals[rep_name] = rep_deals.get(rep_name, 0) + 1
            seen_leads.add(lead_key)

        if date_won == today_str:
            today_revenue += value_dollars
            today_deals += 1

    # Step 3: Meetings (Option A: activity title classification)
    print("  === Meeting classification (Option A) ===", flush=True)
    rep_booked, rep_shown = fetch_meeting_data(year, month, today_str, pst, user_map, name_to_id)
    print(f"  Meetings booked by {len(rep_booked)} reps, shown by {len(rep_shown)} reps.", flush=True)

    # Step 4: Build per-rep data
    all_rep_names = set()
    all_rep_names.update(rep_revenue.keys())
    all_rep_names.update(rep_deals.keys())
    all_rep_names.update(rep_booked.keys())
    all_rep_names.update(REP_QUOTAS.keys())
    all_rep_names -= EXCLUDE_USERS
    all_rep_names -= REVENUE_ONLY_USERS  # revenue counts in totals but no row

    reps = []
    for name in all_rep_names:
        revenue = rep_revenue.get(name, 0)
        deals = rep_deals.get(name, 0)
        quota = REP_QUOTAS.get(name, 0)

        is_deals_only = name in DEALS_ONLY_USERS
        if is_deals_only and deals == 0:
            continue

        booked = 0 if is_deals_only else rep_booked.get(name, 0)
        shown = 0 if is_deals_only else rep_shown.get(name, 0)

        pct_quota = round(revenue / quota * 100, 1) if quota > 0 else 0
        close_rate = round(deals / booked * 100, 1) if booked > 0 else 0
        show_rate = round(shown / booked * 100, 1) if booked > 0 else 0

        reps.append({
            "name": name,
            "revenue": round(revenue, 2),
            "deals": deals,
            "quota": quota,
            "pct_to_quota": pct_quota,
            "booked": booked,
            "shown": shown,
            "close_rate": close_rate,
            "show_rate": show_rate,
            "is_manager": name in MANAGER_USERS,
            "is_lead": name in LEAD_USERS,
            "exclude_meetings": is_deals_only,
        })

    # Step 5: Team totals (computed from raw dicts, includes REVENUE_ONLY_USERS)
    all_counted = set(rep_revenue.keys()) | set(rep_deals.keys()) | set(rep_booked.keys())
    all_counted -= EXCLUDE_USERS
    total_revenue = round(sum(rep_revenue.get(n, 0) for n in all_counted), 2)
    total_deals = sum(rep_deals.get(n, 0) for n in all_counted)
    total_booked = sum(r["booked"] for r in reps)
    total_shown = sum(r["shown"] for r in reps)
    team_close_rate = round(total_deals / total_booked * 100, 1) if total_booked > 0 else 0
    team_show_rate = round(total_shown / total_booked * 100, 1) if total_booked > 0 else 0

    # Step 6: Time context
    working_days_total = count_working_days(year, month)
    working_days_elapsed = count_working_days(year, month, today_day)
    pct_month_passed = round(working_days_elapsed / working_days_total * 100, 1) if working_days_total > 0 else 0
    pct_team_quota = round(total_revenue / TEAM_QUOTA * 100, 1) if TEAM_QUOTA > 0 else 0

    reps.sort(key=lambda r: r["revenue"], reverse=True)

    return {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "month_label": now.strftime("%B %Y"),
        "day_of_month": today_day,
        "days_in_month": last_day,
        "working_days_total": working_days_total,
        "working_days_elapsed": working_days_elapsed,
        "pct_month_passed": pct_month_passed,
        "team_quota": TEAM_QUOTA,
        "pct_team_quota": pct_team_quota,
        "total_revenue": round(total_revenue, 2),
        "total_deals": total_deals,
        "total_booked": total_booked,
        "total_shown": total_shown,
        "team_close_rate": team_close_rate,
        "team_show_rate": team_show_rate,
        "today_revenue": round(today_revenue, 2),
        "today_deals": today_deals,
        "reps": reps,
    }


if __name__ == "__main__":
    data = build_dashboard_data()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    output_path = os.path.join(repo_root, "data.json")

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    # Auto-archive
    archive_dir = os.path.join(repo_root, "archives")
    os.makedirs(archive_dir, exist_ok=True)
    from zoneinfo import ZoneInfo
    pst_now = datetime.now(ZoneInfo("America/Los_Angeles"))
    archive_name = f"data_{pst_now.strftime('%Y-%m')}.json"
    archive_path = os.path.join(archive_dir, archive_name)
    with open(archive_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"   Archive saved: archives/{archive_name}", flush=True)

    # Update archive index
    index_path = os.path.join(archive_dir, "index.json")
    archive_files = sorted(
        [f for f in os.listdir(archive_dir) if f.startswith("data_") and f.endswith(".json")],
        reverse=True,
    )
    months = []
    for af in archive_files:
        ym = af.replace("data_", "").replace(".json", "")
        months.append({"file": af, "key": ym})
    with open(index_path, "w") as f:
        json.dump({"months": months}, f, indent=2)
    print(f"   Archive index updated: {len(months)} month(s)", flush=True)

    print(f"\n=== Dashboard data written to {output_path} ===", flush=True)
    print(f"   Month: {data['month_label']} (day {data['day_of_month']})")
    print(f"   Total Revenue: ${data['total_revenue']:,.2f}")
    print(f"   Today Revenue: ${data['today_revenue']:,.2f}")
    print(f"   Total Deals: {data['total_deals']}")
    print(f"   Meetings Booked: {data['total_booked']} (title-classified)")
    print(f"   Meetings Shown: {data['total_shown']}")
    print(f"   Reps tracked: {len(data['reps'])}")
