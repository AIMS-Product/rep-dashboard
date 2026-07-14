#!/usr/bin/env python3
"""
Fetch sales data from Close CRM and generate data.json for the GitHub Pages dashboard.

Data collected:
  1. Closed/Won opportunities (MTD + today) -> revenue & deal counts per rep
  2. Leads with "First Sales Call Booked Date" in current month -> meetings booked per rep
  3. Lead-level "First Call Show Up (Opp)" = "Yes" -> meetings shown per rep
  4. Close rate = deals closed / meetings booked

Meeting methodology:
  - Queries leads by "First Sales Call Booked Date" custom field (1st of month through today)
  - This field is populated by a separate updater script using title classification
  - One lead = one booked count (field is per-lead, no dedup needed)
  - Excludes leads in "Canceled (by Lead)" or "Outside the US" status
  - Attribution via Lead Owner custom field
"""

import json
import os
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
CF_FIRST_SALES_CALL_BOOKED_ID   = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
CF_FIRST_SALES_CALL_BOOKED_NAME = "First Sales Call Booked Date"

CF_FIRST_CALL_SHOW_ID   = "cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq"
CF_FIRST_CALL_SHOW_NAME = "First Call Show Up (Opp)"

CF_LEAD_OWNER_ID         = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
CF_LEAD_OWNER_NAME       = "Lead Owner"

CF_BTC_BUSINESS_LINE_ID  = "cf_aJlNlilQZIgLLuhcymNN8fiOzewnFxrbWjLZFPmsucO"

# Allowed BTC Business Line values for REVENUE_ONLY_USERS (blank also allowed)
ALLOWED_BUSINESS_LINES = {"Vendingpreneurs (VP)", ""}

# Funnel field for meeting exclusions
CF_FUNNEL_NAME_DEAL_ID  = "cf_xqDQE8fkPsWa0RNEve7hcaxKblCe6489XeZGRDzyPdX"

# Funnels excluded from meeting booked/shown counts
EXCLUDED_FUNNELS = {"LTF - Quiz Funnel"}

# Team quota by month (year, month) -> amount
TEAM_QUOTAS = {
    (2026, 3):  906_000,
    (2026, 4):  1_006_000,
    (2026, 5):  1_000_000,
    (2026, 6):  670_000,
    (2026, 7):  750_000,
    (2026, 8):  1_100_000,
    (2026, 9):  1_100_000,
    (2026, 10): 1_100_000,
    (2026, 11): 1_100_000,
    (2026, 12): 1_100_000,
}
DEFAULT_TEAM_QUOTA = 1_100_000  # fallback for months not listed

REP_QUOTAS = {
    # Lane 1
    "Christian Hartwell": 50_000,   # lead (half quota)
    "Scott Seymour": 100_000,
    "Robin Perkins": 100_000,
    "Eric Piccione": 100_000,
    "Dubem Adindu": 100_000,
    "Zac Clover": 0,            # ramping
    # Lane 2
    "Jason Aaron": 25_000,          # lead (ramping quota)
    "Kelly Schrader": 25_000,
}

# Fully excluded from all dashboard data (revenue, deals, meetings)
EXCLUDE_USERS = {"Mallory Kent", "Unknown", "Ahmad Bukhari", "Stephen Olivas", "Spencer Reynolds"}

# Only appear on dashboard if they have closed deals that month
# Meeting data N/A'd out — only show on Revenue Closed and Opps Closed
DEALS_ONLY_USERS = {"Kristin Nelson", "Joe Dysert"}

# Revenue and meeting counts toward team totals but rep never appears as a row
REVENUE_ONLY_USERS = {"William Chase", "Jordan Humphrey", "Andrea Shoop", "Julia Scaroni", "Ategeka Musinguzi", "Ryan Jones", "Vince Bartolini", "Erick Aguero", "Steven Starnes", "Chris Wanke", "Bryan Barcus", "John Kirk", "Cameron Caswell", "Elvis Ellis", "Jacob Hepner", "Jake Skinner", "Lyle Hubbard", "Luis Galarza", "Juan Cajina"}

# Managers: no quota, show "(mgr)" label, no "Ramping" badge
MANAGER_USERS = {"Joe Dysert"}

# Team leads: show "(lead)" label
LEAD_USERS = {"Christian Hartwell", "Jason Aaron"}


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
    users = {}
    skip = 0
    limit = 100

    while True:
        params = {"_skip": str(skip), "_limit": str(limit)}
        data = api_get("/user/", params)
        for u in data.get("data", []):
            first = u.get("first_name", "")
            last = u.get("last_name", "")
            full = f"{first} {last}".strip()
            users[u["id"]] = full
        if not data.get("has_more", False):
            break
        skip += limit

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


# --- Meeting data (field-based: "First Sales Call Booked Date") ---

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


def fetch_meeting_data(year, month, today_str, user_map, name_to_id):
    """Query leads by "First Sales Call Booked Date" field for the current month.

    One lead = one booked count. No title classification or dedup needed —
    the field is per-lead and already reflects the true first booking date.
    Returns (rep_booked, rep_shown) dicts.
    """
    date_gte = f"{year}-{month:02d}-01"
    date_lte = today_str  # cap at today, no future-dated counts

    query_str = (
        f'"First Sales Call Booked Date" >= "{date_gte}" '
        f'"First Sales Call Booked Date" <= "{date_lte}"'
    )

    # Paginate lead query
    all_leads = []
    skip = 0
    limit = 200

    while True:
        params = {
            "query": query_str,
            "_skip": str(skip),
            "_limit": str(limit),
        }
        data = api_get("/lead/", params)
        leads = data.get("data", [])
        all_leads.extend(leads)
        if not data.get("has_more", False):
            break
        skip += limit

    print(f"  Found {len(all_leads)} leads with First Sales Call Booked Date in range.", flush=True)

    # Attribute and count
    rep_booked = {}
    rep_shown = {}
    excluded_status = 0
    excluded_funnel = 0

    for lead in all_leads:
        # Exclude by lead status
        if lead.get("status_id", "") in EXCLUDED_LEAD_STATUSES:
            excluded_status += 1
            continue

        custom = lead.get("custom", {})
        merged = dict(custom)
        for k, v in lead.items():
            if k.startswith("custom."):
                merged[k] = v
                merged[k.replace("custom.", "")] = v

        # Exclude by funnel (e.g., LTF - Quiz Funnel)
        funnel = get_custom_value(merged, CF_FUNNEL_NAME_DEAL_ID, "Funnel Name DEAL (Opp)")
        if str(funnel).strip() in EXCLUDED_FUNNELS:
            excluded_funnel += 1
            continue

        owner_raw = get_custom_value(merged, CF_LEAD_OWNER_ID, CF_LEAD_OWNER_NAME)
        rep_name = resolve_owner_to_name(owner_raw, user_map, name_to_id)

        if rep_name in EXCLUDE_USERS:
            continue

        rep_booked[rep_name] = rep_booked.get(rep_name, 0) + 1

        # Shown
        show_up = get_custom_value(merged, CF_FIRST_CALL_SHOW_ID, CF_FIRST_CALL_SHOW_NAME)
        if str(show_up).strip().lower() == "yes":
            rep_shown[rep_name] = rep_shown.get(rep_name, 0) + 1

    print(f"  Excluded {excluded_status} leads (Canceled/Outside US status)", flush=True)
    if excluded_funnel:
        print(f"  Excluded {excluded_funnel} leads (excluded funnel: LTF - Quiz Funnel)", flush=True)
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
    lead_bl_cache = {}  # cache lead business line lookups
    bl_excluded = 0

    for opp in opps:
        user_id = opp.get("user_id")
        rep_name = user_map.get(user_id, "Unknown")
        if rep_name in EXCLUDE_USERS:
            continue

        # For REVENUE_ONLY users, check BTC Business Line on the lead
        # Only count VP or blank — skip BK, AOC, etc.
        if rep_name in REVENUE_ONLY_USERS:
            lead_id = opp.get("lead_id", "")
            if lead_id and lead_id not in lead_bl_cache:
                try:
                    lead_data = api_get(f"/lead/{lead_id}")
                    custom = lead_data.get("custom", {})
                    bl_val = custom.get("BTC Business Line", custom.get(CF_BTC_BUSINESS_LINE_ID, ""))
                    lead_bl_cache[lead_id] = str(bl_val).strip() if bl_val else ""
                except Exception:
                    lead_bl_cache[lead_id] = ""  # default to blank (allowed)

            bl = lead_bl_cache.get(lead_id, "")
            if bl not in ALLOWED_BUSINESS_LINES:
                bl_excluded += 1
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

    if bl_excluded:
        print(f"  Excluded {bl_excluded} opps from REVENUE_ONLY users (non-VP business line)", flush=True)

    # Step 3: Meetings (field-based: "First Sales Call Booked Date")
    print("  === Fetching meeting data (First Sales Call Booked Date field) ===", flush=True)
    rep_booked, rep_shown = fetch_meeting_data(year, month, today_str, user_map, name_to_id)
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
    all_counted = set(rep_revenue.keys()) | set(rep_deals.keys()) | set(rep_booked.keys()) | set(rep_shown.keys())
    all_counted -= EXCLUDE_USERS
    total_revenue = round(sum(rep_revenue.get(n, 0) for n in all_counted), 2)
    total_deals = sum(rep_deals.get(n, 0) for n in all_counted)
    total_booked = sum(rep_booked.get(n, 0) for n in all_counted)
    total_shown = sum(rep_shown.get(n, 0) for n in all_counted)
    team_close_rate = round(total_deals / total_booked * 100, 1) if total_booked > 0 else 0
    team_show_rate = round(total_shown / total_booked * 100, 1) if total_booked > 0 else 0

    # Step 6: Time context
    team_quota = TEAM_QUOTAS.get((year, month), DEFAULT_TEAM_QUOTA)
    working_days_total = count_working_days(year, month)
    working_days_elapsed = count_working_days(year, month, today_day)
    pct_month_passed = round(working_days_elapsed / working_days_total * 100, 1) if working_days_total > 0 else 0
    pct_team_quota = round(total_revenue / team_quota * 100, 1) if team_quota > 0 else 0

    reps.sort(key=lambda r: r["revenue"], reverse=True)

    return {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "month_label": now.strftime("%B %Y"),
        "day_of_month": today_day,
        "days_in_month": last_day,
        "working_days_total": working_days_total,
        "working_days_elapsed": working_days_elapsed,
        "pct_month_passed": pct_month_passed,
        "team_quota": team_quota,
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
    print(f"   Meetings Booked: {data['total_booked']} (First Sales Call Booked Date field)")
    print(f"   Meetings Shown: {data['total_shown']}")
    print(f"   Reps tracked: {len(data['reps'])}")
