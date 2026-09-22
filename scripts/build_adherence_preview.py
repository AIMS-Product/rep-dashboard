#!/usr/bin/env python3
"""Add aggregate process-adherence metrics to a rep-dashboard payload from Close CRM.

The adapter performs GET requests only and never serializes lead-level evidence or customer
content.  Its command-line entry point writes ``data.preview.json`` for local review; the production
dashboard fetcher imports ``add_adherence_to_dashboard`` and writes the enriched payload to
``data.json``.
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
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from adherence_rules import aggregate_rep_scores, score_lead, validate_aggregate


BASE_URL = "https://api.close.com/api/v1"
PACIFIC = ZoneInfo("America/Los_Angeles")

CF_FIRST_SALES_CALL_BOOKED_ID = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
CF_FIRST_SALES_CALL_BOOKED_NAME = "First Sales Call Booked Date"
CF_FIRST_CALL_SHOW_ID = "cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq"
CF_FIRST_CALL_SHOW_NAME = "First Call Show Up (Opp)"
CF_LEAD_OWNER_ID = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
CF_LEAD_OWNER_NAME = "Lead Owner"
CF_FUNNEL_NAME_DEAL_ID = "cf_xqDQE8fkPsWa0RNEve7hcaxKblCe6489XeZGRDzyPdX"

EXCLUDED_LEAD_STATUSES = {
    "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT": "canceled_by_lead",
    "stat_aR2jBa8YnTNZmHAnPsnlQuinBdaXpSBCkZGP3UvoBlV": "lost",
    "stat_p3oblSTnbsyDAw4rWqZDePGYMOlKBgV2FjbqIMDrfvF": "disqualified",
    "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB": "outside_us",
    "stat_U9MI7pqsvIjceTv3pCU7b1EghO8Q83h1HUcL6fGVyi6": "do_not_contact",
}
EXCLUDED_FUNNELS = {"LTF - Quiz Funnel"}
ACTIVITY_ENDPOINTS = {
    "emails": "/activity/email/",
    "sms": "/activity/sms/",
    "notes": "/activity/note/",
    "meetings": "/activity/meeting/",
    "task_completions": "/activity/task_completed/",
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


def chunks(values: list[str], size: int = 25):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def fetch_users(client: CloseClient) -> dict[str, str]:
    users = {}
    for user in client.paginate("/user/", {"_fields": "id,first_name,last_name"}):
        full_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        users[user["id"]] = full_name
    return users


def fetch_cohort(client: CloseClient, year: int, month: int) -> list[dict[str, Any]]:
    last_day = monthrange(year, month)[1]
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-{last_day:02d}"
    query = f'"{CF_FIRST_SALES_CALL_BOOKED_NAME}" >= "{start}" "{CF_FIRST_SALES_CALL_BOOKED_NAME}" <= "{end}"'
    return list(client.paginate("/lead/", {"query": query}))


def fetch_activities(
    client: CloseClient,
    lead_ids: list[str],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    by_kind: dict[str, dict[str, list[dict[str, Any]]]] = {}
    total_batches = max(1, (len(lead_ids) + 24) // 25)
    for kind, endpoint in ACTIVITY_ENDPOINTS.items():
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


def add_adherence_to_dashboard(
    dashboard: dict[str, Any],
    *,
    month_override: str | None = None,
    throttle: float = 0.18,
    limit_leads: int | None = None,
    source: str = "close_crm",
    preview_only: bool = False,
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = repo_root.parent
    year, month_number = (
        tuple(map(int, month_override.split("-")))
        if month_override
        else month_from_label(dashboard["month_label"])
    )

    api_key = load_env_value([workspace_root / ".env", repo_root / ".env"], "CLOSE_API_KEY")
    if not api_key:
        raise RuntimeError("CLOSE_API_KEY was not found in the environment or workspace .env")
    client = CloseClient(api_key, throttle=throttle)

    print(f"Building read-only Close adherence for {year}-{month_number:02d}", flush=True)
    print("  Fetching users and monthly cohort", flush=True)
    users_by_id = fetch_users(client)
    ids_by_name = {name: user_id for user_id, name in users_by_id.items()}
    raw_cohort = fetch_cohort(client, year, month_number)
    dashboard_reps = {
        row["name"]: row
        for row in dashboard.get("reps") or []
        if not row.get("exclude_meetings")
    }

    included = []
    exclusion_counts = defaultdict(int)
    for lead in raw_cohort:
        status_exclusion = EXCLUDED_LEAD_STATUSES.get(lead.get("status_id"))
        if status_exclusion:
            exclusion_counts[status_exclusion] += 1
            continue
        funnel = str(custom_value(lead, CF_FUNNEL_NAME_DEAL_ID, "Funnel Name DEAL (Opp)") or "").strip()
        if funnel in EXCLUDED_FUNNELS:
            exclusion_counts["funnel"] += 1
            continue
        owner_name, owner_id = resolve_owner(
            custom_value(lead, CF_LEAD_OWNER_ID, CF_LEAD_OWNER_NAME),
            users_by_id,
            ids_by_name,
        )
        if owner_name not in dashboard_reps or not owner_id:
            exclusion_counts["not_visible_rep"] += 1
            continue
        booked_date = str(custom_value(lead, CF_FIRST_SALES_CALL_BOOKED_ID, CF_FIRST_SALES_CALL_BOOKED_NAME) or "")[:10]
        if not booked_date:
            exclusion_counts["missing_booked_date"] += 1
            continue
        included.append({
            "id": lead["id"],
            "owner_id": owner_id,
            "owner_name": owner_name,
            "booked_date": booked_date,
            "show_state": str(custom_value(lead, CF_FIRST_CALL_SHOW_ID, CF_FIRST_CALL_SHOW_NAME) or ""),
        })

    if limit_leads:
        included = included[:limit_leads]
    lead_ids = [lead["id"] for lead in included]
    print(f"  Cohort: {len(raw_cohort):,} raw, {len(included):,} included", flush=True)
    print(f"  Exclusions: {dict(exclusion_counts)}", flush=True)

    print("  Fetching cohort activities in lead-ID batches", flush=True)
    activity = fetch_activities(client, lead_ids)
    print("  Fetching cohort tasks", flush=True)
    tasks_by_lead = fetch_tasks_by_lead(client, lead_ids)

    now = datetime.now(PACIFIC)
    evidence_by_rep: dict[str, list[dict[str, dict[str, bool]]]] = defaultdict(list)
    for lead in included:
        lead_id = lead["id"]
        evidence_by_rep[lead["owner_id"]].append(score_lead(
            booked_date=lead["booked_date"],
            show_state=lead["show_state"],
            owner_id=lead["owner_id"],
            emails=activity["emails"].get(lead_id, []),
            sms=activity["sms"].get(lead_id, []),
            notes=activity["notes"].get(lead_id, []),
            meetings=activity["meetings"].get(lead_id, []),
            tasks=tasks_by_lead.get(lead_id, []),
            task_completions=activity["task_completions"].get(lead_id, []),
            now=now,
        ))

    aggregates = aggregate_rep_scores(evidence_by_rep)
    empty = aggregate_rep_scores({"empty": []})["empty"]
    rep_summary = []
    for row in dashboard.get("reps") or []:
        rep_id = ids_by_name.get(row["name"])
        row["rep_owner_id"] = rep_id
        row["adherence"] = aggregates.get(rep_id, empty)
        validate_aggregate(row["adherence"])
        rep_summary.append({
            "name": row["name"],
            "pre": row["adherence"]["pre_call_pct"],
            "post": row["adherence"]["post_call_pct"],
        })

    dashboard["adherence_meta"] = {
        "schema_version": 1,
        "source": source,
        "generated_at": now.isoformat(),
        "period": {
            "month": f"{year}-{month_number:02d}",
            "timezone": "America/Los_Angeles",
            "basis": "first_sales_call_booked_date",
        },
        "cohort": {
            "raw": len(raw_cohort),
            "included": len(included),
            "excluded": sum(exclusion_counts.values()),
            "exclusion_counts": dict(exclusion_counts),
        },
        "api_requests": client.request_count,
        "preview_only": preview_only,
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
    )


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="Dashboard month as YYYY-MM (defaults to data.json month)")
    parser.add_argument("--dashboard-data", help="Source dashboard JSON")
    parser.add_argument("--output", default=str(repo_root / "data.preview.json"))
    parser.add_argument("--throttle", type=float, default=0.18, help="Minimum seconds between Close requests")
    parser.add_argument("--limit-leads", type=int, help="Optional smoke-test cohort limit")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        cli_args = parse_args()
        preview = build_preview(cli_args)
        output_path = Path(cli_args.output)
        output_path.write_text(json.dumps(preview, indent=2) + "\n")
        print(f"Wrote aggregate preview to {output_path}", flush=True)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise
