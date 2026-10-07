#!/usr/bin/env python3
"""Build a private local dashboard preview for the previous Pacific calendar day."""

from __future__ import annotations

import json
import argparse
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from build_adherence_preview import (
    ACTIVITY_ENDPOINTS,
    CloseClient,
    fetch_activities,
    fetch_leads_by_booked_date_range,
    fetch_tasks_by_lead,
    fetch_users,
    load_env_value,
    add_adherence_to_dashboard,
    close_lead_url,
)
from fetch_data import aggregate_meeting_leads, meeting_lead_rows


PACIFIC = ZoneInfo("America/Los_Angeles")
ROOT = Path(__file__).resolve().parents[1]
DAILY_ARCHIVE_ROOT = ROOT / "archives" / "daily"


def daily_show_rate(booked: int, shown: int) -> float:
    return round(shown / booked * 100, 1) if booked else 0


def write_standalone_html(data: dict, output: Path) -> None:
    template = (ROOT / "index.html").read_text().replace(
        "<title>Sales Performance Dashboard</title>",
        "<title>Daily Scorecard | Rep Dashboard</title>",
        1,
    )
    embedded = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    archive_index = DAILY_ARCHIVE_ROOT / "index.json"
    archive_dates = json.loads(archive_index.read_text()).get("dates", []) if archive_index.exists() else []
    embedded_dates = json.dumps(archive_dates, separators=(",", ":"))
    marker = "</head>"
    if marker not in template:
        raise ValueError("index.html is missing its </head> insertion point")
    script = f"<script>window.DAILY_PREVIEW_DATA={embedded};window.DAILY_PREVIEW_MODE=true;window.DAILY_ARCHIVE_DATES={embedded_dates};</script>\n"
    output.write_text(template.replace(marker, script + marker, 1))


def serialize_preview(dashboard: dict) -> dict:
    reps = []
    for rep in dashboard.get("reps", []):
        adherence = dict(rep.get("adherence") or {})
        adherence["lead_cohorts"] = {
            key: {
                bucket: [{field: lead.get(field) for field in ("name", "url", "booked_date", "scored_call_at", "status_label")}
                         for lead in leads]
                for bucket, leads in cohorts.items()
            }
            for key, cohorts in adherence.get("lead_cohorts", {}).items()
        }
        reps.append({
            key: rep[key]
            for key in ("name", "is_manager", "exclude_meetings", "booked", "shown", "qualified", "show_rate")
            if key in rep
        } | {"adherence": adherence,
             "daily_metric_leads": rep.get("daily_metric_leads", {"booked": [], "shown": [], "qualified": []})})
    return {
        "daily_meta": dashboard["daily_meta"],
        "adherence_meta": dashboard["adherence_meta"],
        "daily_metric_leads": dashboard.get("daily_metric_leads", {"booked": [], "shown": [], "qualified": []}),
        "reps": reps,
    }


def write_daily_archive(data: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    data["daily_meta"].get("extract", {}).pop("path", None)
    output.write_text(json.dumps(data, indent=2) + "\n")
    dates = sorted(
        (path.stem.removeprefix("data_") for path in output.parent.glob("data_*.json")),
        reverse=True,
    )
    (output.parent / "index.json").write_text(json.dumps({"dates": dates}, indent=2) + "\n")


def build(
    output: Path,
    extract_path: Path,
    throttle: float = 0.18,
    reuse_extract: bool = False,
    report_date: str | None = None,
    archive: bool = False,
    write_local_preview: bool = True,
) -> dict:
    now = datetime.now(PACIFIC)
    yesterday = now.date() - timedelta(days=1)
    if reuse_extract:
        extract = json.loads(extract_path.read_text())
        day = extract["meta"]["date"]
        yesterday = datetime.fromisoformat(day).date()
    else:
        day = report_date or yesterday.isoformat()
        key = load_env_value([ROOT.parent / ".env", ROOT / ".env"], "CLOSE_API_KEY")
        if not key:
            raise RuntimeError("CLOSE_API_KEY was not found in the environment or workspace .env")

        client = CloseClient(key, throttle=throttle)
        started_at = datetime.now(PACIFIC).isoformat()
        print(f"Reading Close records for {day} (America/Los_Angeles)", flush=True)
        users = fetch_users(client)
        leads = fetch_leads_by_booked_date_range(client, day, day)
        lead_ids = [str(lead["id"]) for lead in leads if lead.get("id")]
        activities = fetch_activities(client, lead_ids)
        tasks = fetch_tasks_by_lead(client, lead_ids)
        extract = {
            "meta": {
                "month": day[:7],
                "date": day,
                "timezone": "America/Los_Angeles",
                "source": "CloseClient in scripts/build_adherence_preview.py",
                "started_at": started_at,
                "ended_at": datetime.now(PACIFIC).isoformat(),
                "api_requests": client.request_count,
                "raw_lead_count": len(leads),
                "lead_ids_with_records": len(lead_ids),
                "activity_counts": {
                    kind: sum(len(rows) for rows in grouped.values())
                    for kind, grouped in activities.items()
                },
                "task_count": sum(len(rows) for rows in tasks.values()),
                "activity_kinds": list(ACTIVITY_ENDPOINTS),
                "task_completion_endpoint": "/activity/task_completed/",
                "complete": True,
            },
            "users": users,
            "leads": leads,
            "activities": {kind: dict(grouped) for kind, grouped in activities.items()},
            "tasks": dict(tasks),
        }
        extract_path.parent.mkdir(parents=True, exist_ok=True)
        extract_path.write_text(json.dumps(extract, indent=2, sort_keys=True) + "\n")
    if not extract.get("meta", {}).get("complete"):
        raise ValueError("Close extract is not marked complete")
    day = extract["meta"]["date"]
    report_day = date.fromisoformat(day)
    users, leads = extract["users"], extract["leads"]

    seed = json.loads((ROOT / "data.json").read_text())
    dashboard = deepcopy(seed)
    name_to_id = {name: user_id for user_id, name in users.items()}
    booked, shown, qualified, excluded_status, excluded_funnel = aggregate_meeting_leads(
        leads, users, name_to_id
    )
    metric_leads = {
        rep["name"]: {"booked": [], "shown": [], "qualified": []}
        for rep in dashboard.get("reps", [])
    }
    team_metric_leads = {"booked": [], "shown": [], "qualified": []}
    for row in meeting_lead_rows(leads, users, name_to_id):
        if row.get("excluded") or row.get("rep") not in metric_leads:
            continue
        lead = row["lead"]
        item = {
            "name": lead.get("display_name") or lead.get("name") or "Unnamed lead",
            "url": close_lead_url(lead),
            "booked_date": day,
            "rep": row["rep"],
        }
        team_metric_leads["booked"].append(item)
        metric_leads.setdefault(row["rep"], {"booked": [], "shown": [], "qualified": []})["booked"].append(item)
        if row["shown"]:
            team_metric_leads["shown"].append(item)
            metric_leads[row["rep"]]["shown"].append(item)
        if row["qualified"]:
            team_metric_leads["qualified"].append(item)
            metric_leads[row["rep"]]["qualified"].append(item)
    for rep in dashboard.get("reps", []):
        rep_name = rep["name"]
        rep["booked"] = booked.get(rep_name, 0)
        rep["shown"] = shown.get(rep_name, 0)
        rep["qualified"] = qualified.get(rep_name, 0)
        rep["show_rate"] = daily_show_rate(rep["booked"], rep["shown"])
        rep["daily_metric_leads"] = metric_leads.get(rep_name, {"booked": [], "shown": [], "qualified": []})
    dashboard["daily_metric_leads"] = team_metric_leads

    dashboard = add_adherence_to_dashboard(
        dashboard,
        month_override=day[:7],
        source="close_daily_fixed_extract",
        preview_only=True,
        extract=extract,
    )
    dashboard["daily_meta"] = {
        "date": day,
        "timezone": "America/Los_Angeles",
        "period_label": report_day.strftime("%A, %B %-d, %Y"),
        "archive_matured": datetime.fromisoformat(extract["meta"]["ended_at"]).date()
        >= date.fromisoformat(day) + timedelta(days=2),
        "extract": {
            "path": str(extract_path),
            "started_at": extract["meta"]["started_at"],
            "ended_at": extract["meta"]["ended_at"],
        },
        "booked_shown_qualified": {
            "raw_leads": len(leads),
            "excluded_status": excluded_status,
            "excluded_funnel": excluded_funnel,
            "team_booked": sum(booked.values()),
            "team_shown": sum(shown.values()),
            "team_qualified": sum(qualified.values()),
        },
    }
    preview = serialize_preview(dashboard)
    if archive:
        write_daily_archive(preview, output)
    if write_local_preview:
        output.write_text(json.dumps(preview, indent=2) + "\n")
        write_standalone_html(preview, ROOT / "daily-preview.local.html")
        write_standalone_html(preview, ROOT / "daily-preview.html")
    print(json.dumps({
        "date": day,
        "raw_leads": len(leads),
        "booked": sum(booked.values()),
        "shown": sum(shown.values()),
        "qualified": sum(qualified.values()),
        "extract": str(extract_path),
        "preview": str(output) if write_local_preview else None,
    }, indent=2), flush=True)
    return dashboard


def main() -> int:
    output = ROOT / "data.yesterday.preview.json"
    extract = ROOT / ".private-audit" / "close-extract-yesterday.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-extract", action="store_true", help="Regenerate using the existing private Close extract")
    parser.add_argument("--extract", type=Path, default=extract, help="Private extract path")
    parser.add_argument("--output", type=Path, help="Output JSON path")
    parser.add_argument("--date", help="Pacific cohort date as YYYY-MM-DD")
    parser.add_argument("--archive", action="store_true", help="Write an aggregate daily archive including lead names and Close links")
    parser.add_argument("--archive-missing", action="store_true", help="Archive missing dates from the last week after the 24-hour follow-up window closes")
    args = parser.parse_args()
    try:
        if args.archive_missing:
            today = datetime.now(PACIFIC).date()
            existing_paths = {
                path.stem.removeprefix("data_"): path
                for path in DAILY_ARCHIVE_ROOT.glob("data_*.json")
            }
            # Archive only dates whose full calendar day ended at least 24 hours ago.
            for offset in range(2, 9):
                target = today - timedelta(days=offset)
                day = target.isoformat()
                existing_path = existing_paths.get(day)
                if existing_path:
                    existing_data = json.loads(existing_path.read_text())
                    if existing_data.get("daily_meta", {}).get("archive_matured"):
                        continue
                build(
                    DAILY_ARCHIVE_ROOT / f"data_{day}.json",
                    ROOT / ".private-audit" / f"close-extract-{day}.json",
                    report_date=day,
                    archive=True,
                    write_local_preview=False,
                )
        else:
            day = args.date or (datetime.now(PACIFIC).date() - timedelta(days=1)).isoformat()
            destination = args.output or (
                DAILY_ARCHIVE_ROOT / f"data_{day}.json" if args.archive else output
            )
            build(destination, args.extract, reuse_extract=args.reuse_extract,
                  report_date=args.date, archive=args.archive,
                  write_local_preview=not args.archive)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
