#!/usr/bin/env python3
"""Add aggregate process-adherence metrics to a rep-dashboard payload from Close CRM.

The adapter performs GET requests only. Its command-line entry point writes a local
``data.preview.json`` with lead-level score cohorts for review. The production dashboard fetcher
imports ``add_adherence_to_dashboard`` and writes only aggregate scores to ``data.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from base64 import b64encode
from calendar import monthrange
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from adherence_rules import LOST_STATUS_ID, STEP_META, WON_STATUS_ID, aggregate_rep_scores, first_call_deadline, parse_datetime, score_lead, validate_aggregate
from qualifying_meeting_rules import latest_qualifying_dates_in_period


BASE_URL = "https://api.close.com/api/v1"
PACIFIC = ZoneInfo("America/Los_Angeles")

CF_FIRST_SALES_CALL_BOOKED_ID = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
CF_FIRST_SALES_CALL_BOOKED_NAME = "First Sales Call Booked Date"
CF_LATEST_SALES_CALL_BOOKED_ID = "cf_2PQJIcagevN5HvUHfmWGWR22pCvzLZk6tJPTicDvuS3"
CF_LATEST_SALES_CALL_BOOKED_NAME = "Latest Sales Call Booked Date"
LATEST_BOOKED_DATE_FIELD = (CF_LATEST_SALES_CALL_BOOKED_ID, CF_LATEST_SALES_CALL_BOOKED_NAME)
CF_FIRST_CALL_SHOW_ID = "cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq"
CF_FIRST_CALL_SHOW_NAME = "First Call Show Up (Opp)"
CF_LEAD_OWNER_ID = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
CF_LEAD_OWNER_NAME = "Lead Owner"
CF_FUNNEL_NAME_DEAL_ID = "cf_xqDQE8fkPsWa0RNEve7hcaxKblCe6489XeZGRDzyPdX"

EXCLUDED_LEAD_STATUSES = {
    "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT": "canceled_by_lead",
    "stat_p3oblSTnbsyDAw4rWqZDePGYMOlKBgV2FjbqIMDrfvF": "disqualified",
    "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB": "outside_us",
    "stat_U9MI7pqsvIjceTv3pCU7b1EghO8Q83h1HUcL6fGVyi6": "do_not_contact",
}
EXCLUDED_FUNNELS = {"LTF - Quiz Funnel"}
ACTIVITY_ENDPOINTS = {
    "emails": "/activity/email/",
    "sms": "/activity/sms/",
    "calls": "/activity/call/",
    "notes": "/activity/note/",
    "meetings": "/activity/meeting/",
}


def load_env_value(paths: Iterable[Path], key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            candidate, value = line.split("=", 1)
            if candidate.strip() == key:
                return value.strip().strip('"').strip("'")
    return ""


class CloseClient:
    def __init__(self, api_key: str, throttle: float = 0.18):
        self.api_key = api_key
        self.throttle = throttle
        self.last_call = 0.0
        self.request_count = 0

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        elapsed = time.monotonic() - self.last_call
        if elapsed < self.throttle:
            time.sleep(self.throttle - elapsed)
        query = urlencode(params or {})
        url = f"{BASE_URL}{endpoint}" + (f"?{query}" if query else "")
        token = b64encode(f"{self.api_key}:".encode()).decode()
        request = Request(url, headers={"Authorization": f"Basic {token}", "Accept": "application/json"})
        for attempt in range(4):
            try:
                self.last_call = time.monotonic()
                self.request_count += 1
                with urlopen(request, timeout=45) as response:
                    return json.loads(response.read().decode())
            except HTTPError as error:
                if error.code == 429 and attempt < 3:
                    wait = 2 ** (attempt + 1)
                    print(f"    Rate limited; retrying in {wait}s", flush=True)
                    time.sleep(wait)
                    continue
                body = error.read().decode(errors="replace")[:500] if error.fp else ""
                raise RuntimeError(f"Close API {error.code} for {endpoint}: {body}") from error
            except (URLError, TimeoutError, OSError) as error:
                if attempt == 3:
                    raise RuntimeError(f"Close API connection failed for {endpoint} after retries") from error
                wait = 2 ** (attempt + 1)
                print(f"    Close connection interrupted; retrying in {wait}s", flush=True)
                time.sleep(wait)
        raise RuntimeError(f"Close API retry budget exhausted for {endpoint}")

    def paginate(self, endpoint: str, params: dict[str, Any] | None = None):
        query = dict(params or {})
        # Activity endpoints cap pages at 100; CRM-core endpoints accept 200.
        query.setdefault("_limit", 100 if endpoint.startswith("/activity/") else 200)
        skip = 0
        while True:
            query["_skip"] = skip
            payload = self.get(endpoint, query)
            rows = payload.get("data") or []
            yield from rows
            if not payload.get("has_more") or not rows:
                return
            skip += len(rows)


def custom_value(lead: dict[str, Any], field_id: str, field_name: str) -> Any:
    for source in (lead, lead.get("custom") or {}):
        for key in (f"custom.{field_id}", field_id, field_name):
            if source.get(key) is not None:
                return source[key]
    return ""


def resolve_owner(
    raw_owner: Any,
    users_by_id: dict[str, str],
    ids_by_name: dict[str, str],
) -> tuple[str, str | None]:
    if isinstance(raw_owner, dict):
        owner_id = raw_owner.get("id")
        owner_name = users_by_id.get(owner_id) or raw_owner.get("name") or "Unknown"
        return owner_name, owner_id
    value = str(raw_owner or "").strip()
    if value in users_by_id:
        return users_by_id[value], value
    if value in ids_by_name:
        return value, ids_by_name[value]
    return value or "Unknown", None


def scoring_rep_for_meeting(
    first_meeting: dict[str, Any] | None,
    current_owner_id: str | None,
    visible_rep_ids: set[str],
) -> tuple[str | None, str]:
    """Choose the meeting's rep, falling back to current owner only without a known rep."""
    if first_meeting:
        primary = str(first_meeting.get("user_id") or "")
        assigned = {str(value) for value in first_meeting.get("users") or [] if value}
        if primary:
            assigned.add(primary)
        if primary in visible_rep_ids:
            return primary, "meeting_rep"
        visible_assigned = assigned & visible_rep_ids
        if len(visible_assigned) == 1:
            return next(iter(visible_assigned)), "meeting_rep"
        if assigned:
            return None, "meeting_rep_not_visible" if not visible_assigned else "ambiguous_meeting_rep"
    if current_owner_id in visible_rep_ids:
        return current_owner_id, "owner_fallback"
    return None, "not_visible_rep"


def chunks(values: list[str], size: int = 25):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def fetch_users(client: CloseClient) -> dict[str, str]:
    users = {}
    for user in client.paginate("/user/", {"_fields": "id,first_name,last_name"}):
        full_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        users[user["id"]] = full_name
    return users


def fetch_cohort(
    client: CloseClient, year: int, month: int,
    *, field_name: str = CF_FIRST_SALES_CALL_BOOKED_NAME,
) -> list[dict[str, Any]]:
    last_day = monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day:02d}"
    return fetch_leads_by_booked_date_range(client, start, end, field_name=field_name)


def fetch_leads_by_booked_date_range(
    client: CloseClient, start: str, end: str,
    *, field_name: str = CF_FIRST_SALES_CALL_BOOKED_NAME,
) -> list[dict[str, Any]]:
    """Read a booked-date cohort using inclusive ISO calendar dates."""
    query = f'"{field_name}" >= "{start}" "{field_name}" <= "{end}"'
    return list(client.paginate("/lead/", {"query": query}))


def fetch_process_cohort(client: CloseClient, start: str, end: str) -> list[dict[str, Any]]:
    """Keep original-date leads when a later booking moves their LSCBD."""
    leads = {
        lead["id"]: lead for lead in fetch_leads_by_booked_date_range(client, start, end)
    }
    leads.update({
        lead["id"]: lead for lead in fetch_leads_by_booked_date_range(
            client, start, end, field_name=CF_LATEST_SALES_CALL_BOOKED_NAME,
        )
    })
    return list(leads.values())


def fetch_qualifying_process_cohort(client: CloseClient, start: str, end: str) -> list[dict[str, Any]]:
    """Select FSCBD leads by qualifying meeting dates within the report period."""
    dates = latest_qualifying_dates_in_period(client.paginate("/activity/meeting/"), start, end)

    leads = {
        str(lead["id"]): lead for lead in fetch_process_cohort(client, start, end)
        if str(lead.get("id")) in dates
    }
    for lead_id in sorted(dates.keys() - leads.keys()):
        payload = client.get(f"/lead/{lead_id}/")
        lead = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
        if not isinstance(lead, dict) or str(lead.get("id")) != lead_id:
            raise ValueError(f"Close did not return lead details for qualifying meeting lead {lead_id}")
        leads[lead_id] = lead

    return [
        dict(lead, _process_candidate_date=dates[lead_id])
        for lead_id, lead in leads.items()
        if custom_value(lead, CF_FIRST_SALES_CALL_BOOKED_ID, CF_FIRST_SALES_CALL_BOOKED_NAME)
    ]


def process_candidate_date(lead: dict[str, Any], start: str, end: str) -> tuple[str, str]:
    activity_date = str(lead.get("_process_candidate_date") or "")[:10]
    if start <= activity_date <= end:
        return activity_date, "meeting_activity"
    latest = str(custom_value(lead, *LATEST_BOOKED_DATE_FIELD) or "")[:10]
    if start <= latest <= end:
        return latest, "latest"
    first = str(custom_value(lead, CF_FIRST_SALES_CALL_BOOKED_ID, CF_FIRST_SALES_CALL_BOOKED_NAME) or "")[:10]
    if start <= first <= end:
        return first, "first_fallback"
    return "", "outside_range"


def has_active_first_meeting(meetings: Iterable[dict[str, Any]], booked_date: str) -> bool:
    """Require a non-canceled meeting on the selected process date."""
    return any(
        (starts := parse_datetime(meeting.get("starts_at") or meeting.get("activity_at"))) is not None
        and starts.date().isoformat() == booked_date
        and not str(meeting.get("status") or "").strip().lower().startswith(
            ("canceled", "cancelled", "declined")
        )
        for meeting in meetings
    )


def fetch_activities(
    client: CloseClient,
    lead_ids: list[str],
    *,
    kinds: Iterable[str] | None = None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    by_kind: dict[str, dict[str, list[dict[str, Any]]]] = {}
    total_batches = max(1, (len(lead_ids) + 24) // 25)
    for kind in ACTIVITY_ENDPOINTS if kinds is None else kinds:
        endpoint = ACTIVITY_ENDPOINTS[kind]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        total = 0
        for index, batch in enumerate(chunks(lead_ids), start=1):
            for row in client.paginate(endpoint, {"lead_id": ",".join(batch)}):
                lead_id = row.get("lead_id")
                if lead_id:
                    grouped[lead_id].append(row)
                    total += 1
            if index == total_batches or index % 5 == 0:
                print(f"    {kind}: batch {index}/{total_batches}", flush=True)
        by_kind[kind] = grouped
        print(f"    {kind}: {total:,} matching activities", flush=True)
    return by_kind


def fetch_tasks_by_lead(
    client: CloseClient,
    lead_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Fetch tasks lead-by-lead so unassigned tasks are included in the baseline."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, lead_id in enumerate(lead_ids, start=1):
        for task in client.paginate("/task/", {"lead_id": lead_id, "view": "all"}):
            grouped[lead_id].append(task)
        if index == len(lead_ids) or index % 50 == 0:
            print(f"    tasks: lead {index}/{len(lead_ids)}", flush=True)
    print(f"    tasks: {sum(len(rows) for rows in grouped.values()):,} matching tasks", flush=True)
    return grouped


def month_from_label(label: str) -> tuple[int, int]:
    parsed = datetime.strptime(label, "%B %Y")
    return parsed.year, parsed.month


def close_lead_url(lead: dict[str, Any]) -> str:
    """Use Close's canonical lead link only when it points to this exact lead."""
    url = str(lead.get("html_url") or "")
    parsed = urlparse(url)
    if (
        parsed.scheme == "https"
        and parsed.netloc == "app.close.com"
        and parsed.path.rstrip("/") == f"/lead/{lead['id']}"
        and not parsed.query
        and not parsed.fragment
    ):
        return url
    return ""


def opportunity_status_label(lead: dict[str, Any]) -> str:
    """Return the latest opportunity status attached to a lead, if any."""
    opportunities = lead.get("opportunities") or []
    if not opportunities:
        return "No opportunity"
    latest = max(
        opportunities,
        key=lambda opportunity: (
            str(opportunity.get("date_updated") or ""),
            str(opportunity.get("date_created") or ""),
            str(opportunity.get("id") or ""),
        ),
    )
    return str(latest.get("status_label") or "Unknown opportunity status")


def build_lead_cohorts(
    scored_leads: list[tuple[dict[str, Any], dict[str, dict[str, bool]]]],
    aggregate: dict[str, Any],
) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Group the exact eligible leads behind each step's numerator and denominator."""
    cohorts: dict[str, dict[str, list[dict[str, str]]]] = {
        key: {"completed": [], "missed": [], "exempt": []} for key in STEP_META
    }
    for lead, evidence in scored_leads:
        summary = {
            "id": lead["id"],
            "name": lead["name"],
            "booked_date": lead["booked_date"],
            "scored_call_at": lead.get("scored_call_at") or "",
            "url": lead.get("url") or "",
            "status_label": lead.get("status_label") or "",
        }
        for key in STEP_META:
            cell = evidence[key]
            if cell.get("exempt"):
                cohorts[key]["exempt"].append(summary)
            elif cell["eligible"]:
                bucket = "completed" if cell["done"] else "missed"
                cohorts[key][bucket].append(summary)
    for key, lists in cohorts.items():
        for rows in lists.values():
            rows.sort(key=lambda row: (row["booked_date"], row["name"].casefold(), row["id"]))
        counts = aggregate["steps"][key]
        if len(lists["completed"]) != counts["done"] or (
            len(lists["completed"]) + len(lists["missed"])
        ) != counts["eligible"]:
            raise ValueError(f"lead cohort does not match aggregate for {key}")
    return cohorts


def build_lead_results(
    scored_leads: list[tuple[dict[str, Any], dict[str, dict[str, bool]]]],
) -> list[dict[str, Any]]:
    """Keep every lead and all seven outcomes available in the private preview."""
    results = []
    for lead, evidence in scored_leads:
        results.append({
            "id": lead["id"],
            "name": lead["name"],
            "url": lead.get("url") or "",
            "status_label": lead.get("status_label") or "",
            "opportunity_status_label": lead.get("opportunity_status_label") or "No opportunity",
            "booked_date": lead["booked_date"],
            "scored_call_at": lead.get("scored_call_at") or "",
            "attribution": lead.get("attribution"),
            "steps": {
                key: ("Exempt" if cell.get("exempt") else "Neutral" if not cell["eligible"] else "Completed" if cell["done"] else "Missed")
                for key, cell in evidence.items()
            },
        })
    return sorted(results, key=lambda row: (row["booked_date"], row["name"].casefold(), row["id"]))


def add_adherence_to_dashboard(
    dashboard: dict[str, Any],
    *,
    month_override: str | None = None,
    throttle: float = 0.18,
    limit_leads: int | None = None,
    source: str = "close_crm",
    preview_only: bool = False,
    extract: dict[str, Any] | None = None,
    include_canceled_by_lead_status: bool = False,
    candidate_booked_date_field: tuple[str, str] | None = None,
    candidate_date_range: tuple[str, str] | None = None,
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = repo_root.parent
    year, month_number = (
        tuple(map(int, month_override.split("-")))
        if month_override
        else month_from_label(dashboard["month_label"])
    )
    period_start, period_end = candidate_date_range or (
        f"{year}-{month_number:02d}-01",
        f"{year}-{month_number:02d}-{monthrange(year, month_number)[1]:02d}",
    )

    client: CloseClient | None = None
    if extract is None:
        api_key = load_env_value([workspace_root / ".env", repo_root / ".env"], "CLOSE_API_KEY")
        if not api_key:
            raise RuntimeError("CLOSE_API_KEY was not found in the environment or workspace .env")
        client = CloseClient(api_key, throttle=throttle)
        print(f"Building read-only Close adherence for {year}-{month_number:02d}", flush=True)
        print("  Fetching users and monthly cohort", flush=True)
        users_by_id = fetch_users(client)
        raw_cohort = (
            fetch_qualifying_process_cohort(client, period_start, period_end)
            if candidate_booked_date_field == LATEST_BOOKED_DATE_FIELD
            else fetch_cohort(client, year, month_number)
        )
    else:
        if extract.get("meta", {}).get("month") != f"{year}-{month_number:02d}":
            raise ValueError("Close extract month does not match the dashboard month")
        if not extract.get("meta", {}).get("complete"):
            raise ValueError("Close extract is not marked complete")
        users_by_id = extract["users"]
        raw_cohort = extract["leads"]
    ids_by_name = {name: user_id for user_id, name in users_by_id.items()}
    dashboard_reps = {
        row["name"]: row
        for row in dashboard.get("reps") or []
        if not row.get("exclude_meetings") and not row.get("is_manager")
    }
    visible_rep_ids = {user_id for user_id, name in users_by_id.items() if name in dashboard_reps}

    candidates = []
    exclusion_counts = defaultdict(int)
    for lead in raw_cohort:
        status_exclusion = EXCLUDED_LEAD_STATUSES.get(lead.get("status_id"))
        if status_exclusion and not (
            include_canceled_by_lead_status and status_exclusion == "canceled_by_lead"
        ):
            exclusion_counts[status_exclusion] += 1
            continue
        funnel = str(custom_value(lead, CF_FUNNEL_NAME_DEAL_ID, "Funnel Name DEAL (Opp)") or "").strip()
        if funnel in EXCLUDED_FUNNELS:
            exclusion_counts["funnel"] += 1
            continue
        _, owner_id = resolve_owner(
            custom_value(lead, CF_LEAD_OWNER_ID, CF_LEAD_OWNER_NAME),
            users_by_id,
            ids_by_name,
        )
        if candidate_booked_date_field == LATEST_BOOKED_DATE_FIELD:
            booked_date, date_source = process_candidate_date(lead, period_start, period_end)
        else:
            date_field_id, date_field_name = candidate_booked_date_field or (
                CF_FIRST_SALES_CALL_BOOKED_ID, CF_FIRST_SALES_CALL_BOOKED_NAME,
            )
            booked_date = str(custom_value(lead, date_field_id, date_field_name) or "")[:10]
            date_source = "first"
        if not booked_date:
            exclusion_counts["missing_booked_date"] += 1
            continue
        candidates.append({
            "id": lead["id"],
            "name": str(lead.get("display_name") or lead.get("name") or "Unnamed lead"),
            "url": close_lead_url(lead),
            "status_label": str(lead.get("status_label") or ""),
            "opportunity_status_label": opportunity_status_label(lead),
            "closed_won": lead.get("status_id") == WON_STATUS_ID
            or "closed / won" in str(lead.get("status_label") or "").lower(),
            "closed_lost": lead.get("status_id") == LOST_STATUS_ID
            or str(lead.get("status_label") or "").strip().lower().endswith("lost"),
            "current_owner_id": owner_id,
            "booked_date": booked_date,
            "date_source": date_source,
            "show_state": str(custom_value(lead, CF_FIRST_CALL_SHOW_ID, CF_FIRST_CALL_SHOW_NAME) or ""),
        })

    if extract is None:
        print("  Fetching candidate first-call meetings", flush=True)
        meetings_by_lead = fetch_activities(
            client, [lead["id"] for lead in candidates], kinds=("meetings",)
        )["meetings"]
    else:
        meetings_by_lead = extract["activities"].get("meetings", {})
    included = []
    for lead in candidates:
        if not has_active_first_meeting(
            meetings_by_lead.get(lead["id"], []), lead["booked_date"]
        ):
            exclusion_counts["inactive_booked_meeting"] += 1
            continue
        first_call_at, first_meeting = first_call_deadline(
            lead["booked_date"], meetings_by_lead.get(lead["id"], []),
            show_state=lead["show_state"],
        )
        rep_id, reason = scoring_rep_for_meeting(
            first_meeting, lead["current_owner_id"], visible_rep_ids
        )
        if not rep_id:
            exclusion_counts[reason] += 1
            continue
        lead["attribution"] = reason
        lead["rep_id"] = rep_id
        lead["scored_call_at"] = first_call_at.isoformat() if first_meeting and first_call_at else ""
        included.append(lead)

    if limit_leads:
        exclusion_counts["preview_limit"] += max(0, len(included) - limit_leads)
        included = included[:limit_leads]
    attribution_counts = defaultdict(int)
    for lead in included:
        attribution_counts[lead["attribution"]] += 1
    lead_ids = [lead["id"] for lead in included]
    print(f"  Cohort: {len(raw_cohort):,} raw, {len(included):,} included", flush=True)
    print(f"  Exclusions: {dict(exclusion_counts)}", flush=True)

    if extract is None:
        print("  Fetching cohort activities in lead-ID batches", flush=True)
        activity = fetch_activities(client, lead_ids, kinds=("emails", "sms", "calls", "notes"))
        activity["meetings"] = meetings_by_lead
        print("  Fetching cohort tasks", flush=True)
        tasks_by_lead = fetch_tasks_by_lead(client, lead_ids)
    else:
        activity = extract["activities"]
        tasks_by_lead = extract["tasks"]

    now = (
        datetime.fromisoformat(extract["meta"]["ended_at"]).astimezone(PACIFIC)
        if extract is not None
        else datetime.now(PACIFIC)
    )
    evidence_by_rep: dict[str, list[dict[str, dict[str, bool]]]] = defaultdict(list)
    scored_leads_by_rep: dict[str, list[tuple[dict[str, Any], dict[str, dict[str, bool]]]]] = defaultdict(list)
    for lead in included:
        lead_id = lead["id"]
        evidence = score_lead(
            booked_date=lead["booked_date"],
            show_state=lead["show_state"],
            owner_id=lead["rep_id"],
            lead_owner_id=lead["current_owner_id"],
            closed_won=lead["closed_won"],
            closed_lost=lead["closed_lost"],
            emails=activity["emails"].get(lead_id, []),
            sms=activity["sms"].get(lead_id, []),
            calls=activity.get("calls", {}).get(lead_id, []),
            notes=activity["notes"].get(lead_id, []),
            meetings=activity["meetings"].get(lead_id, []),
            tasks=tasks_by_lead.get(lead_id, []),
            now=now,
        )
        evidence_by_rep[lead["rep_id"]].append(evidence)
        scored_leads_by_rep[lead["rep_id"]].append((lead, evidence))

    aggregates = aggregate_rep_scores(evidence_by_rep)
    empty = aggregate_rep_scores({"empty": []})["empty"]
    rep_summary = []
    for row in dashboard.get("reps") or []:
        rep_id = ids_by_name.get(row["name"])
        row["rep_owner_id"] = rep_id
        row["adherence"] = aggregates.get(rep_id, empty)
        validate_aggregate(row["adherence"])
        # The production dashboard's adherence drilldown needs the lead names and
        # Close links behind each aggregate. Keep the full per-lead result matrix
        # limited to the private audit preview.
        row["adherence"]["lead_cohorts"] = build_lead_cohorts(
            scored_leads_by_rep.get(rep_id, []), row["adherence"]
        )
        if preview_only:
            row["adherence"]["lead_results"] = build_lead_results(
                scored_leads_by_rep.get(rep_id, [])
            )
        rep_summary.append({
            "name": row["name"],
            "pre": row["adherence"]["pre_call_pct"],
            "post": row["adherence"]["post_call_pct"],
        })

    dashboard["adherence_meta"] = {
        "schema_version": 9 if preview_only else 6,
        "source": source,
        "generated_at": now.isoformat(),
        "period": {
            "month": f"{year}-{month_number:02d}",
            "timezone": "America/Los_Angeles",
            "basis": "latest_sales_call_booked_date_with_first_date_fallback"
            if candidate_booked_date_field == LATEST_BOOKED_DATE_FIELD
            else "first_sales_call_booked_date",
        },
        "cohort": {
            "raw": len(raw_cohort),
            "included": len(included),
            "excluded": sum(exclusion_counts.values()),
            "exclusion_counts": dict(exclusion_counts),
            "attribution_counts": dict(attribution_counts),
            "includes_lost": True,
        },
        "api_requests": client.request_count if client else extract["meta"]["api_requests"],
        "preview_only": preview_only,
    }
    if extract is not None:
        dashboard["adherence_meta"]["extract"] = {
            "started_at": extract["meta"]["started_at"],
            "ended_at": extract["meta"]["ended_at"],
            "complete": extract["meta"]["complete"],
        }
    print("  Rep baseline:", flush=True)
    for summary in rep_summary:
        print(f"    {summary['name']}: pre={summary['pre']} post={summary['post']}", flush=True)
    return dashboard


def build_preview(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    dashboard_path = Path(args.dashboard_data or repo_root / "data.json")
    dashboard = json.loads(dashboard_path.read_text())
    return add_adherence_to_dashboard(
        dashboard,
        month_override=args.month,
        throttle=args.throttle,
        limit_leads=args.limit_leads,
        source="close_local_baseline",
        preview_only=True,
        candidate_booked_date_field=LATEST_BOOKED_DATE_FIELD,
        include_canceled_by_lead_status=True,
    )


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="Dashboard month as YYYY-MM (defaults to data.json month)")
    parser.add_argument("--dashboard-data", help="Source dashboard JSON")
    parser.add_argument("--output", default=str(repo_root / "data.preview.json"))
    parser.add_argument("--throttle", type=float, default=0.18, help="Minimum seconds between Close requests")
    parser.add_argument("--limit-leads", type=int, help="Optional smoke-test cohort limit")
    parser.add_argument("--extract", help="Use a previously captured private Close extract")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        cli_args = parse_args()
        if cli_args.extract:
            repo_root = Path(__file__).resolve().parents[1]
            dashboard_path = Path(cli_args.dashboard_data or repo_root / "data.json")
            dashboard = json.loads(dashboard_path.read_text())
            extract = json.loads(Path(cli_args.extract).read_text())
            preview = add_adherence_to_dashboard(
                dashboard, month_override=cli_args.month, throttle=cli_args.throttle,
                limit_leads=cli_args.limit_leads, source="close_fixed_extract",
                preview_only=True, extract=extract,
                candidate_booked_date_field=LATEST_BOOKED_DATE_FIELD,
                include_canceled_by_lead_status=True,
            )
        else:
            preview = build_preview(cli_args)
        output_path = Path(cli_args.output)
        output_path.write_text(json.dumps(preview, indent=2) + "\n")
        print(f"Wrote local preview to {output_path}", flush=True)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise
