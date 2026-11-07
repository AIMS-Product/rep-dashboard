#!/usr/bin/env python3
"""Build a local, read-only MTD V2 shadow report from Close."""

from __future__ import annotations

import concurrent.futures
import argparse
import json
import os
import re
import sys
import time
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .aggregates import BOOKING_SCOPE, build_breakdowns, vp_closer_bookings

ROOT = Path(__file__).resolve().parent
PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc
RULES = json.loads((ROOT / "rules.json").read_text())
API_ROOT = "https://api.close.com/api/v1/"
UTM_CAMPAIGN_FIELD = "cf_jnbd0xzUY3tuxzxiGxBs2hONuExeXMvAoTUM2R64Lq3"
UTM_CONTENT_FIELD = "cf_R7o66i0XPycLQHlxOLbIqk6c6j3oB8CzxF3e3apI1hn"
QUALIFIED_FIELD = "cf_ZDx7NBQaDzV1yYrFcBMzt6cIYj81dAcswpNN0CQzCPS"
PROGRAM_TIER_FIELD = "cf_XvdC8hcwyfkoOFn6ElNdGEWbd567Th65m4spLuugYm3"
VENDHUB_PLAN_FIELD = "cf_Y3lcmlnhjmmCCfDgWfoNbA7gXZgvTX7fCcvNNxD9TSp"
LEAD_OWNER_FIELD = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
# The existing MTD's VendHub contribution per won opportunity.
VENDHUB_ONLY_TIERS = {"VendHub - Basic $1,188", "VendHub - Pro $2,999", "Vendhub Renewal- $1,200"}
ARR_BY_TIER = {"Bronze - $5,997": 1200.0, "Silver - $8,997": 2998.0, "Scale - $14,997": 2998.0}
COMPLETED_OUTCOME_ID = "outcome_032Djn4dfeNuEoCunojA7K"
PAGE_SIZE = 100
MAX_WORKERS = 5
REPEAT_WINDOW = timedelta(days=30)
FOLLOWUP = re.compile(r"follow[-\s]?up|fallow\s+up|\bf/u\b|next\s+steps|reschedul", re.I)
NON_BOOKING = re.compile(r"enrollment|silver\s+start\s+up|bronze\s+enrollment|\bq\s*&\s*a\b|walkthrough", re.I)
CANCEL_TITLE = re.compile(r"^\s*(canceled|cancelled)\b", re.I)


def load_api_key() -> str:
    key = os.environ.get("CLOSE_API_KEY")
    if key:
        return key
    env_file = ROOT.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("CLOSE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("Set CLOSE_API_KEY in the environment or repository .env")


class CloseReader:
    def __init__(self, key: str):
        self.key = key
        self.local = threading.local()

    def _session(self) -> requests.Session:
        if not getattr(self.local, "session", None):
            self.local.session = requests.Session()
            self.local.session.auth = (self.key, "")
            self.local.session.headers.update({"Content-Type": "application/json"})
        return self.local.session

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(6):
            try:
                response = self._session().get(API_ROOT + endpoint.lstrip("/"), params=params or {}, timeout=60)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 5:
                    raise
                time.sleep(min(2 ** attempt, 20))
                continue
            if response.status_code == 429:
                time.sleep(float(response.headers.get("Retry-After", min(2 ** attempt, 30))))
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"Close API kept rate limiting GET {endpoint}")

    def pages(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        skip = 0
        while True:
            query = dict(params or {}, _limit=PAGE_SIZE, _skip=skip)
            page = self.get(endpoint, query)
            batch = page.get("data") or []
            rows.extend(batch)
            if endpoint == "contact/" and skip and skip % 5000 == 0:
                print(f"  Read {skip:,} MTD contacts...", flush=True)
            if endpoint == "activity/meeting/" and skip and skip % 5000 == 0:
                print(f"  Read {skip:,} Close meetings...", flush=True)
            if not page.get("has_more"):
                return rows
            if not batch:
                raise RuntimeError(f"Empty Close page with has_more=true at {endpoint} offset {skip}")
            skip += len(batch)

    def meeting_catalog(self) -> list[dict[str, Any]]:
        fields = "id,lead_id,user_id,title,date_created,starts_at,status,outcome_id"
        rows = self.pages("activity/meeting/", {"_fields": fields})
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            activity_id = row.get("id")
            if not activity_id:
                raise RuntimeError("Close meeting row is missing its activity ID")
            if activity_id in by_id and by_id[activity_id] != row:
                raise RuntimeError(f"Conflicting duplicate meeting ID {activity_id}")
            by_id[activity_id] = row
        return list(by_id.values())

    def meetings_created_in_window(self, start_utc: str, end_utc: str) -> list[dict[str, Any]]:
        fields = "id,lead_id,user_id,title,date_created,starts_at,status,outcome_id"
        return self.pages("activity/meeting/", {
            "date_created__gte": start_utc, "date_created__lte": end_utc, "_fields": fields,
        })

    def meeting_history_for_leads(self, lead_ids: set[str]) -> list[dict[str, Any]]:
        fields = "id,lead_id,user_id,title,date_created,starts_at,status,outcome_id"
        rows_by_id: dict[str, dict[str, Any]] = {}
        lead_list = sorted(lead_ids)
        for offset in range(0, len(lead_list), 25):
            batch = lead_list[offset:offset + 25]
            for row in self.pages("activity/meeting/", {"lead_id": ",".join(batch), "_fields": fields}):
                activity_id = row.get("id")
                if not activity_id:
                    raise RuntimeError("Close meeting history row is missing its activity ID")
                if activity_id in rows_by_id and rows_by_id[activity_id] != row:
                    raise RuntimeError(f"Conflicting duplicate meeting ID {activity_id}")
                rows_by_id[activity_id] = row
        return list(rows_by_id.values())


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def custom(record: dict[str, Any], field_id: str) -> str | None:
    value = record.get(f"custom.{field_id}")
    if value is None:
        value = (record.get("custom") or {}).get(field_id)
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("name") or value.get("label")
    return str(value).strip() if value is not None and str(value).strip() else None


def normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def utc_window(start_day: date, end_exclusive: datetime) -> tuple[str, str]:
    start = datetime.combine(start_day, day_time.min, tzinfo=PACIFIC).astimezone(UTC)
    return start.isoformat().replace("+00:00", "Z"), end_exclusive.astimezone(UTC).isoformat().replace("+00:00", "Z")


def request_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "lead_id": record.get("lead_id"),
        "display_name": record.get("display_name") or record.get("name") or "Unknown lead",
        "date_created": record.get("date_created"),
        "status_id": record.get("status_id"),
        "funnel": custom(record, RULES["lead_fields"]["funnel"]),
        "setter_name": custom(record, RULES["lead_fields"]["setter_name"]),
        "qualified": custom(record, QUALIFIED_FIELD),
        "program_tier": custom(record, PROGRAM_TIER_FIELD),
        "vendhub_plan": custom(record, VENDHUB_PLAN_FIELD),
        "lead_owner": record.get(f"custom.{LEAD_OWNER_FIELD}", (record.get("custom") or {}).get(LEAD_OWNER_FIELD)),
        "business_line": custom(record, RULES["lead_fields"]["offer"]),
    }


def fetch_lead(reader: CloseReader, lead_id: str) -> dict[str, Any]:
    fields = ["id", "display_name", "date_created", "status_id", f"custom.{RULES['lead_fields']['funnel']}", f"custom.{RULES['lead_fields']['setter_name']}", f"custom.{QUALIFIED_FIELD}", f"custom.{RULES['lead_fields']['offer']}", f"custom.{PROGRAM_TIER_FIELD}"]
    fields.extend([f"custom.{VENDHUB_PLAN_FIELD}", f"custom.{LEAD_OWNER_FIELD}"])
    result = reader.get(f"lead/{lead_id}/", {"_fields": ",".join(fields)})
    return request_fields(result)


def fetch_contacts(reader: CloseReader, lead_id: str) -> list[dict[str, Any]]:
    fields = f"id,custom.{RULES['contact_utm_fields']['source']},custom.{UTM_CAMPAIGN_FIELD},custom.{UTM_CONTENT_FIELD}"
    return reader.pages("contact/", {"lead_id": lead_id, "_fields": fields})


def fetch_contacts_created_in_window(reader: CloseReader, start_utc: str, end_utc: str) -> list[dict[str, Any]]:
    fields = f"id,lead_id,date_created,custom.{RULES['contact_utm_fields']['source']},custom.{UTM_CAMPAIGN_FIELD},custom.{UTM_CONTENT_FIELD}"
    start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    cursor = start
    result: dict[str, dict[str, Any]] = {}
    while cursor < end:
        segment_end = min(cursor + timedelta(days=1), end)
        for contact in reader.pages("contact/", {
            "date_created__gte": cursor.isoformat().replace("+00:00", "Z"),
            "date_created__lte": segment_end.isoformat().replace("+00:00", "Z"),
            "_fields": fields,
        }):
            if contact.get("id"):
                result[str(contact["id"])] = contact
        cursor = segment_end
    return list(result.values())


def source_resolve(lead: dict[str, Any], contacts: list[dict[str, Any]]) -> tuple[str, str]:
    labels = {label.casefold(): label for label in RULES["marketing_sources"]}
    current = lead.get("funnel")
    if current and current.casefold() in labels:
        return labels[current.casefold()], "lead_funnel_origin"
    recognized: set[str] = set()
    unknown = False
    for contact in contacts:
        raw = custom(contact, RULES["contact_utm_fields"]["source"])
        if not raw:
            continue
        key = raw.casefold().split("&", 1)[0].split("?", 1)[0]
        resolved = RULES["utm_source_map"].get(key)
        if resolved:
            recognized.add(resolved)
        else:
            unknown = True
    if len(recognized) == 1 and not unknown:
        return next(iter(recognized)), "contact_utm_origin"
    return "Unknown (Needs Review)", "unresolved_contact_origins"


def attribution_review(lead: dict[str, Any], contacts: list[dict[str, Any]]) -> tuple[str, str] | tuple[None, None]:
    """Record why a lead remains unresolved under the approved V2 source rules."""
    channel, _ = source_resolve(lead, contacts)
    if channel != "Unknown (Needs Review)":
        return None, None

    source_values = set()
    for contact in contacts:
        raw = custom(contact, RULES["contact_utm_fields"]["source"])
        if raw:
            source_values.add(raw.casefold().split("&", 1)[0].split("?", 1)[0])
    if not source_values:
        return "unrecoverable_under_v2_rules", "no_utm_source_value"

    mapped = {RULES["utm_source_map"][value] for value in source_values if value in RULES["utm_source_map"]}
    unmapped = source_values - RULES["utm_source_map"].keys()
    if len(mapped) > 1:
        reason = "conflicting_mapped_sources"
    elif mapped and unmapped:
        reason = "mapped_and_unmapped_source_values"
    else:
        reason = "utm_source_value_not_in_approved_map"
    return "unrecoverable_under_v2_rules", reason


def recognized_lead_source(lead: dict[str, Any]) -> bool:
    value = str(lead.get("funnel") or "").casefold()
    return value in {label.casefold() for label in RULES["marketing_sources"]}


def contact_utm(contacts: list[dict[str, Any]]) -> tuple[str, str]:
    campaign = content = None
    for contact in contacts:
        campaign = campaign or custom(contact, UTM_CAMPAIGN_FIELD)
        content = content or custom(contact, UTM_CONTENT_FIELD)
    return campaign or "Unattributed", content or "Unattributed"


def title_bucket(meeting: dict[str, Any], lead: dict[str, Any]) -> tuple[str, str | None, str | None]:
    title = str(meeting.get("title") or "")
    created_at = parse_time(meeting.get("date_created"))
    rules = RULES["title_rules"]
    for rule in rules:
        retired_at = parse_time(rule.get("retired_at_exclusive"))
        if retired_at and created_at and created_at >= retired_at:
            continue
        if re.search(rule["pattern"], title, re.I):
            required = rule.get("required_setter")
            if required and normalized(required) != normalized(lead.get("setter_name")):
                continue
            lane_bucket = "Social DMs" if rule.get("setter_name") == RULES["candidate_policy"]["social_dm_setter"] else "Lane 2"
            return lane_bucket, rule.get("setter_name"), rule.get("key")
    contact_sources = {
        (custom(contact, RULES["contact_utm_fields"]["source"]) or "").casefold()
        for contact in lead.get("contacts", [])
    }
    if contact_sources.intersection(RULES.get("pearl_accepted_utm_sources", [])):
        if any(re.search(pattern, title, re.I) for pattern in RULES.get("pearl_source_title_patterns", [])):
            return "Social DMs", "Pearl Sathekge", "pearl_social_source_title"
    if FOLLOWUP.search(title) or NON_BOOKING.search(title) or CANCEL_TITLE.search(title):
        return "", None, None
    if any(re.search(pattern, title, re.I) for pattern in RULES["new_call_patterns"]):
        if meeting.get("user_id") in set(RULES["excluded_callers"] + RULES["setter_only_callers"]):
            return "", None, None
        return "Marketing", None, "new_call_pattern"
    return "", None, None


def preliminary_bucket(meeting: dict[str, Any]) -> str:
    """Fast title-only prefilter before lead/contact reads."""
    title = str(meeting.get("title") or "")
    created = parse_time(meeting.get("date_created"))
    cutoff = parse_time(RULES.get("historical_cutoff_exclusive"))
    for rule in RULES["title_rules"]:
        retired_at = parse_time(rule.get("retired_at_exclusive"))
        if retired_at and created and created >= retired_at:
            continue
        if rule.get("historical") and created and cutoff and created >= cutoff:
            continue
        if re.search(rule["pattern"], title, re.I):
            return "Social DMs" if rule.get("setter_name") == RULES["candidate_policy"]["social_dm_setter"] else "Lane 2"
    if any(re.search(pattern, title, re.I) for pattern in RULES.get("pearl_source_title_patterns", [])):
        return "Social DMs"
    if FOLLOWUP.search(title) or NON_BOOKING.search(title) or CANCEL_TITLE.search(title):
        return ""
    if meeting.get("user_id") in set(RULES["excluded_callers"] + RULES["setter_only_callers"]):
        return ""
    if any(re.search(pattern, title, re.I) for pattern in RULES["new_call_patterns"]):
        return "Marketing"
    return ""


def cancellation(meeting: dict[str, Any], outcome_label: str) -> bool:
    policy = RULES["candidate_policy"]
    status = normalized(meeting.get("status"))
    outcome = normalized(outcome_label)
    return (
        status in policy["cancellation_statuses"]
        or outcome in policy["cancellation_outcomes"]
        or bool(policy.get("canceled_title_prefix") and CANCEL_TITLE.search(str(meeting.get("title") or "")))
    )


def is_current_active(meeting: dict[str, Any]) -> bool:
    return normalized(meeting.get("status")) in {"upcoming", "in-progress", "completed"}


def choose_bucket_credits(
    candidate_rows: list[dict[str, Any]], all_meetings: list[dict[str, Any]], outcomes: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_lead_all: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for meeting in all_meetings:
        if meeting.get("lead_id"):
            by_lead_all[str(meeting["lead_id"])].append(meeting)
    candidates_by_lead: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        if row["bucket"] in {"Lane 2", "Social DMs"}:
            candidates_by_lead[row["lead_id"]].append(row)
    suppressed: Counter[str] = Counter()
    chosen_ids: set[str] = set()
    for lead_id, rows in candidates_by_lead.items():
        events = [
            meeting for meeting in by_lead_all.get(lead_id, [])
            if not cancellation(meeting, outcomes.get(str(meeting.get("outcome_id")) or "", ""))
            and parse_time(meeting.get("starts_at")) is not None
        ]
        # A qualifying episode is anchored by any non-canceled scheduled meeting.
        # The first qualifying title in each episode earns the single bucket credit.
        ordered = sorted(events, key=lambda item: (parse_time(item.get("starts_at")), parse_time(item.get("date_created")) or datetime.max.replace(tzinfo=UTC), str(item.get("id") or "")))
        last_meeting_start: datetime | None = None
        for event in ordered:
            start = parse_time(event.get("starts_at"))
            if start is None:
                continue
            candidate = next((row for row in rows if row["meeting_id"] == event.get("id")), None)
            if last_meeting_start is not None and start - last_meeting_start <= REPEAT_WINDOW:
                if candidate:
                    suppressed["within_30_day_episode"] += 1
                last_meeting_start = start
                continue
            last_meeting_start = start
            if candidate:
                chosen_ids.add(candidate["meeting_id"])
        # Every non-canceled scheduled meeting advances the lead's 30-day clock,
        # even when its title does not earn a first-call credit.
    counted = [row for row in candidate_rows if row["bucket"] == "Marketing" or row["meeting_id"] in chosen_ids]
    return counted, dict(suppressed)


def fetch_created_leads(reader: CloseReader, start_date: date, as_of: datetime) -> list[dict[str, Any]]:
    start_utc, end_utc = utc_window(start_date, as_of)
    query = f'date_created >= "{start_utc}" AND date_created <= "{end_utc}"'
    fields = f"id,display_name,date_created,status_id,custom.{RULES['lead_fields']['funnel']},custom.{RULES['lead_fields']['setter_name']},custom.{QUALIFIED_FIELD},custom.{RULES['lead_fields']['offer']},custom.{PROGRAM_TIER_FIELD}"
    fields += f",custom.{VENDHUB_PLAN_FIELD},custom.{LEAD_OWNER_FIELD}"
    return reader.pages("lead/", {"query": query, "_fields": fields})


def fetch_won_opportunities(reader: CloseReader, start_date: date, as_of: datetime) -> list[dict[str, Any]]:
    start = datetime.combine(start_date, day_time.min, tzinfo=PACIFIC).date().isoformat()
    end = (as_of.astimezone(PACIFIC) - timedelta(microseconds=1)).date().isoformat()
    return reader.pages("opportunity/", {
        "status_type": "won", "date_won__gte": start, "date_won__lte": end,
        "_fields": "id,lead_id,value,date_won,user_id,currency",
    })


def yes(value: Any) -> bool:
    return normalized(value) in {"yes", "true", "1"}


def safe_money(raw: Any) -> float:
    try:
        # Close values are cents in the established MTD reader.
        return float(str(raw).split()[0].replace(",", "").replace("$", "")) / 100
    except (ValueError, TypeError, IndexError):
        return 0.0


def build_dataset(reader: CloseReader, as_of: datetime | None = None, start_date: date | None = None) -> dict[str, Any]:
    as_of = as_of or datetime.now(PACIFIC)
    if as_of.tzinfo is None:
        raise ValueError("The source cutoff must include a timezone")
    as_of = as_of.astimezone(PACIFIC)
    start_date = start_date or as_of.date().replace(day=1)
    if datetime.combine(start_date, day_time.min, tzinfo=PACIFIC) >= as_of:
        raise ValueError("The report start must precede its exclusive cutoff")
    start_utc, end_utc = utc_window(start_date, as_of)
    start_bound = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    end_bound = as_of.astimezone(UTC)
    print("Reading MTD Close meetings (read-only)...", flush=True)
    mtd_meetings = reader.meetings_created_in_window(start_utc, end_utc)
    mtd_meetings = [
        meeting for meeting in mtd_meetings
        if (created := parse_time(meeting.get("date_created"))) is not None
        and start_bound <= created < end_bound
    ]
    outcomes = {str(item.get("id")): str(item.get("label") or item.get("name") or "") for item in reader.pages("outcome/")}
    candidate_meetings = [
        meeting for meeting in mtd_meetings
        if meeting.get("lead_id")
        and is_current_active(meeting)
        and not cancellation(meeting, outcomes.get(str(meeting.get("outcome_id") or ""), ""))
        and parse_time(meeting.get("starts_at")) is not None
        and preliminary_bucket(meeting)
    ]
    history_lead_ids = {
        str(row["lead_id"]) for row in candidate_meetings
        if preliminary_bucket(row) in {"Lane 2", "Social DMs"}
    }
    print(f"Candidate first-call meetings: {len(candidate_meetings)}; reading histories for {len(history_lead_ids)} Lane 2/Social DM leads...", flush=True)
    meeting_history = reader.meeting_history_for_leads(history_lead_ids)
    leads_created = fetch_created_leads(reader, start_date, as_of)
    leads_created = [
        lead for lead in leads_created
        if (created := parse_time(lead.get("date_created"))) is not None
        and start_bound <= created < end_bound
    ]
    excluded_lead_statuses = set(RULES.get("excluded_lead_status_ids", []))
    leads_created = [row for row in leads_created if row.get("status_id") not in excluded_lead_statuses]
    won = fetch_won_opportunities(reader, start_date, as_of)
    lead_ids = {str(m["lead_id"]) for m in candidate_meetings}
    lead_ids.update(str(l["id"]) for l in leads_created if l.get("id"))
    lead_ids.update(str(row["lead_id"]) for row in won if row.get("lead_id"))
    lead_cache: dict[str, dict[str, Any]] = {str(row["id"]): request_fields(row) for row in leads_created if row.get("id")}
    missing = sorted(lead_ids - lead_cache.keys())
    print(f"Reading lead details for {len(missing)} booking leads...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for result in pool.map(lambda lid: fetch_lead(reader, lid), missing):
            lead_cache[str(result["id"])] = result
    created_by_id = {str(row["id"]): lead_cache[str(row["id"])] for row in leads_created if row.get("id")}
    candidate_lead_ids = {str(row["lead_id"]) for row in candidate_meetings}
    won_lead_ids = {str(row["lead_id"]) for row in won if row.get("lead_id")}
    # Pull contact attribution for booked leads (campaign/content drilldowns) and
    # won leads only. The Close contacts list endpoint cannot filter the MTD
    # creation window or accept a bulk list of lead IDs, so scanning every
    # unbooked MTD lead would require one API request per lead.
    # Won leads also need campaign/content attribution for revenue drilldowns.
    contact_ids = candidate_lead_ids | won_lead_ids
    contact_cache: dict[str, list[dict[str, Any]]] = defaultdict(list)
    print(f"Reading contact attribution for {len(contact_ids)} booked or won leads...", flush=True)
    if contact_ids:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for lead_id, contacts in zip(sorted(contact_ids), pool.map(lambda lid: fetch_contacts(reader, lid), sorted(contact_ids))):
                contact_cache[lead_id] = contacts

    candidate_rows: list[dict[str, Any]] = []
    for meeting in candidate_meetings:
        lead_id = str(meeting["lead_id"])
        lead = lead_cache[lead_id]
        status = normalized(meeting.get("status"))
        outcome = outcomes.get(str(meeting.get("outcome_id") or ""), "")
        if cancellation(meeting, outcome) or not is_current_active(meeting):
            continue
        booked_at = parse_time(meeting.get("date_created"))
        starts_at = parse_time(meeting.get("starts_at"))
        if not booked_at or not starts_at:
            continue
        lead_for_title = {**lead, "contacts": contact_cache[lead_id]}
        bucket, setter, rule = title_bucket(meeting, lead_for_title)
        if not bucket:
            continue
        campaign, content = contact_utm(contact_cache[lead_id])
        channel, source_basis = source_resolve(lead, contact_cache[lead_id])
        attribution_status, attribution_reason = attribution_review(lead, contact_cache[lead_id])
        candidate_rows.append({
            "meeting_id": str(meeting["id"]), "lead_id": lead_id,
            "lead_name": lead.get("display_name") or "Unknown lead", "title": meeting.get("title") or "",
            "booked_at": booked_at.isoformat(), "booked_date_pt": booked_at.astimezone(PACIFIC).date().isoformat(),
            "starts_at": starts_at.isoformat(), "scheduled_date_pt": starts_at.astimezone(PACIFIC).date().isoformat(),
            "status": status, "outcome": outcome or "No outcome", "bucket": bucket,
            "setter": setter or "Marketing", "rule": rule or "", "channel": channel, "source_basis": source_basis,
            "attribution_status": attribution_status, "attribution_reason": attribution_reason,
            "utm_campaign": campaign, "utm_content": content,
            "close_url": f"https://app.close.com/lead/{lead_id}/",
            "showed": normalized(outcome) == "completed",
            "qualified": yes(lead.get("qualified")),
            "owner": lead.get("lead_owner"),
        })
    counted, suppressions = choose_bucket_credits(candidate_rows, meeting_history, outcomes)
    users = {row["id"]: " ".join(filter(None, [row.get("first_name"), row.get("last_name")]))
             for row in reader.pages("user/")}
    all_owner_count = len(counted)
    counted = vp_closer_bookings(counted, users)

    channel_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "leads_created": 0, "bookings": 0, "marketing": 0, "lane2": 0, "social_dms": 0,
        "showed": 0, "qualified_leads": set(), "closed_won": 0, "revenue": 0.0, "drilldowns": {},
        "unrecoverable_leads": set(), "unrecoverable_reasons": defaultdict(set),
    })
    for lead in leads_created:
        lead_id = str(lead.get("id") or "")
        if not lead_id:
            continue
        channel, _ = source_resolve(lead_cache.get(lead_id, request_fields(lead)), contact_cache.get(lead_id, []))
        channel_rows[channel]["leads_created"] += 1
    for row in counted:
        entry = channel_rows[row["channel"]]
        if row.get("attribution_status") == "unrecoverable_under_v2_rules":
            entry["unrecoverable_leads"].add(row["lead_id"])
            entry["unrecoverable_reasons"][row["attribution_reason"]].add(row["lead_id"])
        entry["bookings"] += 1
        entry[{"Marketing": "marketing", "Lane 2": "lane2", "Social DMs": "social_dms"}[row["bucket"]]] += 1
        entry["showed"] += int(row["showed"])
        if row["qualified"]:
            entry["qualified_leads"].add(row["lead_id"])
        campaign_key = row["utm_campaign"]
        content_key = row["utm_content"]
        drilldown = entry["drilldowns"].setdefault(campaign_key, {})
        details = drilldown.setdefault(content_key, {"bookings": 0, "marketing": 0, "lane2": 0, "social_dms": 0, "showed": 0, "rows": []})
        details["bookings"] += 1
        details[{"Marketing": "marketing", "Lane 2": "lane2", "Social DMs": "social_dms"}[row["bucket"]]] += 1
        details["showed"] += int(row["showed"])
        details["rows"].append(row)

    excluded_closers = set(RULES.get("excluded_closers", []))
    vendhub_arr = 0.0
    accepted_won = []
    for opportunity in won:
        lead_id = str(opportunity.get("lead_id") or "")
        if not lead_id or opportunity.get("user_id") in excluded_closers:
            continue
        lead = lead_cache.get(lead_id)
        if not lead:
            lead = fetch_lead(reader, lead_id)
            lead_cache[lead_id] = lead
            if not recognized_lead_source(lead):
                contact_cache[lead_id] = fetch_contacts(reader, lead_id)
        channel, _ = source_resolve(lead, contact_cache.get(lead_id, []))
        currency = str(opportunity.get("currency") or "").strip().upper()
        if currency and currency != "USD":
            continue
        channel_rows[channel]["closed_won"] += 1
        value = safe_money(opportunity.get("value"))
        channel_rows[channel]["revenue"] += value
        tier = lead.get("program_tier")
        vendhub_arr += value if tier in VENDHUB_ONLY_TIERS else ARR_BY_TIER.get(tier, 0.0)
        campaign, content = contact_utm(contact_cache.get(lead_id, []))
        plan = lead.get("vendhub_plan")
        accepted_won.append({
            "lead_id": lead_id, "channel": channel, "revenue": value,
            "utm_campaign": campaign, "utm_content": content,
            "package": (tier or "Unknown") + (f" · {plan}" if plan else ""),
            "setter": lead.get("setter_name") or "Marketing / Unassigned",
            "owner": opportunity.get("user_id"),
        })

    output_rows = []
    for channel, entry in sorted(channel_rows.items(), key=lambda item: (-item[1]["bookings"], item[0])):
        entry["qualified"] = len(entry.pop("qualified_leads"))
        entry["unrecoverable_booked_leads"] = len(entry.pop("unrecoverable_leads"))
        entry["unrecoverable_reason_counts"] = {
            reason: len(lead_ids) for reason, lead_ids in entry.pop("unrecoverable_reasons").items()
        }
        entry["drilldowns"] = [
            {"campaign": campaign, "content": content_rows}
            for campaign, content_rows in sorted(entry["drilldowns"].items(), key=lambda item: (-sum(x["bookings"] for x in item[1].values()), item[0]))
        ]
        for group in entry["drilldowns"]:
            group["content"] = [
                {"name": content, **details}
                for content, details in sorted(group["content"].items(), key=lambda item: (-item[1]["bookings"], item[0]))
            ]
        output_rows.append({"channel": channel, **entry})
    totals = {key: sum(row[key] for row in output_rows) for key in ("leads_created", "bookings", "marketing", "lane2", "social_dms", "showed", "qualified", "closed_won", "revenue")}
    # These rollups consume the already-selected credits and won cohort above.
    breakdowns = build_breakdowns(counted, accepted_won, users)
    return {
        "generated_at": as_of.isoformat(), "cutoff_pt": as_of.strftime("%b %-d, %Y %I:%M %p PT"),
        "source_cutoff": as_of.isoformat(),
        "period": f"{start_date.isoformat()} through {(as_of - timedelta(microseconds=1)).date().isoformat()} PT",
        "vendhub_arr": vendhub_arr,
        "breakdowns": breakdowns,
        "rules_version": RULES["version"], "mtd_meetings_scanned": len(mtd_meetings),
        "booking_scope": BOOKING_SCOPE,
        "excluded_owner_bookings": all_owner_count - len(counted),
        "meeting_history_rows_loaded": len(meeting_history),
        "candidate_meetings": len(candidate_rows), "counted_meetings": len(counted),
        "excluded_repeat_candidates": suppressions.get("within_30_day_episode", 0),
        "lead_source_note": "Lead-created total is complete. Source by channel uses the lead's Close funnel when recognized; contact UTM fallback is loaded for booked and won leads, so unbooked leads without a recognized funnel appear as Unknown (Needs Review). Booking campaign/content drilldowns use contact UTM data.",
        "attribution_reviewed_at": as_of.isoformat(),
        "rows": counted, "channels": output_rows, "totals": totals,
    }


def main() -> int:
    try:
        parser = argparse.ArgumentParser(description="Build the local MTD V2 shadow preview")
        parser.add_argument("--reuse-existing-data", action="store_true", help="Render the local preview from the last saved data.json without pulling Close")
        parser.add_argument("--as-of", help="Use an explicit ISO timestamp as the source cutoff")
        args = parser.parse_args()
        data_path = ROOT / "public" / "data.json"
        if args.reuse_existing_data:
            if not data_path.exists():
                raise RuntimeError("No existing public/data.json snapshot is available")
            data = json.loads(data_path.read_text())
        else:
            reader = CloseReader(load_api_key())
            as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
            data = build_dataset(reader, as_of)
        output = ROOT / "public" / "data.json"
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        output.write_text(serialized + "\n")
        from publish_shadow import public_data, render_report
        render_report(public_data(output), ROOT / "public" / "index.html")
        print(f"Built local V2 shadow preview: {ROOT / 'public' / 'index.html'}")
        print("Totals: " + json.dumps(data["totals"], sort_keys=True))
        print(f"Counted {data['counted_meetings']} qualifying first-call credits; no review rows emitted.")
        return 0
    except Exception as exc:
        print(f"V2 build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
