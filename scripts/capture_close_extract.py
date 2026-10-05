#!/usr/bin/env python3
"""Capture a private, fixed Close read for an independent adherence audit."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_adherence_preview import (
    ACTIVITY_ENDPOINTS,
    CloseClient,
    fetch_activities,
    fetch_cohort,
    fetch_tasks_by_lead,
    fetch_users,
    load_env_value,
)


PACIFIC = ZoneInfo("America/Los_Angeles")


def capture(month: str, output: Path, throttle: float = 0.18) -> dict:
    repo_root = Path(__file__).resolve().parents[1]
    year, month_number = map(int, month.split("-"))
    key = load_env_value([repo_root.parent / ".env", repo_root / ".env"], "CLOSE_API_KEY")
    if not key:
        raise RuntimeError("CLOSE_API_KEY was not found in the environment or workspace .env")

    extract = {
        "meta": {
            "month": month,
            "timezone": "America/Los_Angeles",
            "source": "CloseClient in scripts/build_adherence_preview.py",
            "started_at": datetime.now(PACIFIC).isoformat(),
        }
    }
    client = CloseClient(key, throttle=throttle)
    users = fetch_users(client)
    leads = fetch_cohort(client, year, month_number)
    lead_ids = [str(lead["id"]) for lead in leads if lead.get("id")]
    activities = fetch_activities(client, lead_ids)
    tasks = fetch_tasks_by_lead(client, lead_ids)
    extract.update({
        "users": users,
        "leads": leads,
        "activities": {kind: dict(by_lead) for kind, by_lead in activities.items()},
        "tasks": dict(tasks),
    })
    extract["meta"].update({
        "ended_at": datetime.now(PACIFIC).isoformat(),
        "api_requests": client.request_count,
        "raw_lead_count": len(leads),
        "lead_ids_with_records": len(lead_ids),
        "activity_counts": {
            kind: sum(len(rows) for rows in by_lead.values())
            for kind, by_lead in activities.items()
        },
        "task_count": sum(len(rows) for rows in tasks.values()),
        "activity_kinds": list(ACTIVITY_ENDPOINTS),
        "task_completion_endpoint": "/activity/task_completed/",
        "complete": True,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(extract, indent=2, sort_keys=True) + "\n")
    return extract["meta"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="Dashboard month as YYYY-MM")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--throttle", type=float, default=0.18)
    args = parser.parse_args()
    try:
        print(json.dumps(capture(args.month, args.output, args.throttle), indent=2))
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
