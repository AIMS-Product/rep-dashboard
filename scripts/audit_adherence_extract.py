#!/usr/bin/env python3
"""Independently reconcile a dashboard preview against a fixed private Close extract."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from math import floor
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
ROLLOUT = datetime(2026, 7, 23, 18, 42, 25, tzinfo=UTC)
WON_STATUS_ID = "stat_WnFc0uhjcjV0cc3bVzdFVqDz7av6rbsOmOvHUsO6s03"
LOST_STATUS_ID = "stat_aR2jBa8YnTNZmHAnPsnlQuinBdaXpSBCkZGP3UvoBlV"
STEP_LABELS = {
    "loom_usage": ("Pre-call", "Pre-Call Loom"),
    "precall_text": ("Pre-call", "Pre-call text"),
    "day_of_confirmation_text": ("Pre-call", "Day-of confirmation"),
    "task_created": ("Post-call", "Task created"),
    "fu_meeting_created": ("Post-call", "FU meeting created"),
    "recap_email": ("Post-call", "Recap email sent"),
}
EXCLUDED_STATUSES = {
    "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT": "canceled_by_lead",
    "stat_p3oblSTnbsyDAw4rWqZDePGYMOlKBgV2FjbqIMDrfvF": "disqualified",
    "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB": "outside_us",
    "stat_U9MI7pqsvIjceTv3pCU7b1EghO8Q83h1HUcL6fGVyi6": "do_not_contact",
}
OMIT_FUNNELS = {"LTF - Quiz Funnel"}
OMIT_MEETING_STATES = {"canceled", "declined-by-lead", "declined-by-org"}
OUTBOUND = {"outbound", "outgoing"}
LOOM = re.compile(r"(^|[^a-z])loom\.com", re.IGNORECASE)
FIELDS = {
    "booked": ("cf_2PQJIcagevN5HvUHfmWGWR22pCvzLZk6tJPTicDvuS3", "Latest Sales Call Booked Date"),
    "first_booked": ("cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq", "First Sales Call Booked Date"),
    "show": ("cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq", "First Call Show Up (Opp)"),
    "owner": ("cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd", "Lead Owner"),
    "funnel": ("cf_xqDQE8fkPsWa0RNEve7hcaxKblCe6489XeZGRDzyPdX", "Funnel Name DEAL (Opp)"),
}


def close_value(lead: dict, key: str) -> Any:
    field_id, field_name = FIELDS[key]
    for source in (lead, lead.get("custom") or {}):
        for name in (f"custom.{field_id}", field_id, field_name):
            if source.get(name) is not None:
                return source[name]
    return ""


def pacific(value: Any, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max if end_of_day else time.min)
    else:
        raw = str(value).strip()
        try:
            if len(raw) == 10:
                parsed_date = date.fromisoformat(raw)
                parsed = datetime.combine(parsed_date, time.max if end_of_day else time.min)
            else:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PACIFIC)
    return parsed.astimezone(PACIFIC)


def stamp(row: dict) -> datetime | None:
    for key in ("activity_at", "date_sent", "date_created"):
        value = pacific(row.get(key))
        if value:
            return value
    return None


def live(row: dict) -> bool:
    return str(row.get("status") or "").strip().lower() not in {"deleted", "archived"}


def sent(row: dict) -> bool:
    if not live(row):
        return False
    status = str(row.get("status") or "").strip().lower()
    if status in {"sent", "completed"}:
        return True
    occurred = stamp(row)
    return bool(occurred and occurred.astimezone(UTC) < ROLLOUT and status in {"", "draft", "outbox"})


def message_stamp(row: dict) -> datetime | None:
    actual = pacific(row.get("date_sent")) if sent(row) else None
    return actual or stamp(row)


def body(row: dict) -> str:
    return "\n".join(str(row.get(k) or "") for k in ("body_text", "text", "note", "body_html", "note_html")).strip()


def citation(kind: str, row: dict, occurred: datetime | None = None) -> dict:
    when = occurred or stamp(row)
    return {"kind": kind, "id": row.get("id"), "at": when.isoformat() if when else None,
            "status": row.get("status"), "user_id": row.get("user_id"),
            "assigned_to": row.get("assigned_to"), "users": row.get("users"),
            "due_at": row.get("date"), "created_at": row.get("date_created")}


def by_lead(extract: dict, kind: str, lead_id: str) -> list[dict]:
    return extract["activities"].get(kind, {}).get(lead_id, [])


def owner_id(lead: dict, users: dict[str, str]) -> str | None:
    raw = close_value(lead, "owner")
    if isinstance(raw, dict):
        return str(raw.get("id") or "") or None
    value = str(raw or "").strip()
    if value in users:
        return value
    matches = [uid for uid, name in users.items() if name == value]
    return matches[0] if len(matches) == 1 else None


def deadline_for(booked: str, meetings: list[dict], show_state: str = "") -> tuple[datetime | None, dict | None]:
    booked_end = pacific(booked, end_of_day=True)
    if not booked_end:
        return None, None
    same_day = []
    for meeting in meetings:
        if str(meeting.get("status") or "").lower() in OMIT_MEETING_STATES:
            continue
        starts = pacific(meeting.get("starts_at") or meeting.get("activity_at"))
        if starts and starts.date() == booked_end.date():
            same_day.append((starts, meeting))
    if same_day:
        return min(same_day, key=lambda pair: pair[0])
    if str(show_state or "").strip().lower() not in {"yes", "no"}:
        later = []
        for meeting in meetings:
            if str(meeting.get("status") or "").lower() in OMIT_MEETING_STATES:
                continue
            starts = pacific(meeting.get("starts_at") or meeting.get("activity_at"))
            if starts and starts.date() > booked_end.date():
                later.append((starts, meeting))
        if later:
            return min(later, key=lambda pair: pair[0])
    return booked_end, None


def credited_rep(meeting: dict | None, lead_owner: str | None, visible: set[str]) -> tuple[str | None, str]:
    if meeting:
        primary = str(meeting.get("user_id") or "")
        assigned = {str(x) for x in meeting.get("users") or [] if x}
        if primary:
            assigned.add(primary)
        if primary in visible:
            return primary, "meeting_rep"
        in_dashboard = assigned & visible
        if len(in_dashboard) == 1:
            return next(iter(in_dashboard)), "meeting_rep"
        if assigned:
            return None, "meeting_rep_not_visible" if not in_dashboard else "ambiguous_meeting_rep"
    if lead_owner in visible:
        return lead_owner, "owner_fallback"
    return None, "not_visible_rep"


def in_scope(lead: dict, start: str, end: str) -> tuple[str | None, str, str]:
    status_reason = EXCLUDED_STATUSES.get(lead.get("status_id"))
    if status_reason and status_reason != "canceled_by_lead":
        return None, status_reason, ""
    if str(close_value(lead, "funnel") or "").strip() in OMIT_FUNNELS:
        return None, "funnel", ""
    activity_date = str(lead.get("_process_candidate_date") or "")[:10]
    if start <= activity_date <= end:
        return activity_date, "candidate", "meeting_activity"
    latest = str(close_value(lead, "booked") or "")[:10]
    if start <= latest <= end:
        return latest, "candidate", "latest"
    first = str(close_value(lead, "first_booked") or "")[:10]
    if start <= first <= end:
        return first, "candidate", "first_fallback"
    return None, "missing_booked_date", ""


def active_first_meeting(extract: dict, lead_id: str, booked_date: str) -> bool:
    return any(
        (starts := pacific(meeting.get("starts_at") or meeting.get("activity_at"))) is not None
        and starts.date().isoformat() == booked_date
        and not str(meeting.get("status") or "").strip().lower().startswith(
            ("canceled", "cancelled", "declined")
        )
        for meeting in by_lead(extract, "meetings", lead_id)
    )


def event(kind: str, row: dict, when: datetime | None = None) -> dict:
    return citation(kind, row, when)


def classify(lead: dict, rep: str, booked: str, lead_owner: str | None,
             extract: dict, now: datetime) -> tuple[dict[str, dict], datetime | None, dict | None]:
    lid = lead["id"]
    sms, emails, notes, calls = (
        by_lead(extract, k, lid) for k in ("sms", "emails", "notes", "calls")
    )
    meetings = by_lead(extract, "meetings", lid)
    tasks = extract.get("tasks", {}).get(lid, [])
    show_value = str(close_value(lead, "show") or "")
    deadline, first = deadline_for(booked, meetings, show_value)
    result = {key: {"result": "Neutral", "support": [], "basis": ""} for key in STEP_LABELS}
    if not deadline:
        return result, None, None

    eligible = deadline <= now
    all_comms = [("email", x, message_stamp(x)) for x in emails] + [("sms", x, message_stamp(x)) for x in sms] + [("note", x, stamp(x)) for x in notes]
    loom_evidence = [event(kind, row, at) for kind, row, at in all_comms
                     if at and at <= deadline and live(row) and LOOM.search(body(row))
                     and (kind != "sms" or (lead_owner and str(row.get("user_id") or "") == lead_owner))]
    result["loom_usage"] = {"result": ("Completed" if loom_evidence else "Missed") if eligible else "Neutral",
                            "support": loom_evidence, "basis": "Pre-call deadline has passed; any active note/email body containing loom.com, or owner-sent SMS body containing loom.com, by deadline counts."}

    precall = [event("sms", msg, at) for msg in sms if (at := message_stamp(msg)) and at <= deadline
               and str(msg.get("direction") or "").lower() in OUTBOUND and sent(msg) and body(msg)
               and lead_owner and str(msg.get("user_id") or "") == lead_owner]
    result["precall_text"] = {"result": ("Completed" if precall else "Missed") if eligible else "Neutral",
                               "support": precall, "basis": "Pre-call deadline has passed; non-empty sent outbound SMS from the current Lead Owner at or before deadline counts."}

    confirmation_eligible = bool(first and lead_owner and eligible)
    confirmation = [event("sms", msg, at) for msg in sms if (at := message_stamp(msg))
                    and at.date() == deadline.date() and at < deadline
                    and str(msg.get("user_id") or "") == lead_owner
                    and str(msg.get("direction") or "").lower() in OUTBOUND and sent(msg) and body(msg)]
    confirmation.extend(
        event("call", call, at)
        for call in calls
        if (at := stamp(call))
        and at.date() == deadline.date()
        and at < deadline
        and str(call.get("user_id") or "") == lead_owner
        and str(call.get("direction") or "").strip().lower() in OUTBOUND
        and live(call)
    )
    result["day_of_confirmation_text"] = {
        "result": ("Completed" if confirmation else "Missed") if confirmation_eligible else "Neutral",
        "support": confirmation, "basis": "Eligible at first call start when same-day meeting and current lead owner are known; a non-empty owner-sent outbound SMS or any owner-made outbound call on that Pacific date before start counts."}

    # Keep post-call eligibility and its anchor independent of the show-up field.
    # Later meetings are next-step evidence, not a reason to delay this anchor.
    post_deadline, post_first = deadline_for(booked, meetings, "Yes")
    anchor = (pacific(post_first.get("starts_at") or post_first.get("activity_at"))
              or post_deadline) if post_first else post_deadline or deadline
    set_eligible = anchor <= now
    def task_ok(task: dict) -> bool:
        due = pacific(task.get("date"), end_of_day=True)
        created_at = pacific(task.get("date_created"))
        assigned = task.get("assigned_to")
        return bool(task.get("lead_id") and task.get("date") and assigned and str(assigned) == rep
                    and str(task.get("_type") or "lead") == "lead"
                    and ((due and due >= anchor) or (created_at and created_at >= anchor)))
    qualifying_tasks = [task for task in tasks if task_ok(task)]
    later_meetings = []
    for mtg in meetings:
        starts = pacific(mtg.get("starts_at") or mtg.get("activity_at"))
        if (starts and starts > anchor and str(mtg.get("status") or "").lower() not in OMIT_MEETING_STATES):
            later_meetings.append(mtg)
    task_evidence = [event("task", task, pacific(task.get("date"), end_of_day=True)) for task in qualifying_tasks]
    meeting_evidence = [event("meeting", mtg, pacific(mtg.get("starts_at") or mtg.get("activity_at")))
                        for mtg in later_meetings]
    result["task_created"] = {
        "result": ("Completed" if task_evidence else "Missed") if set_eligible else "Neutral",
        "support": task_evidence,
        "basis": "Scheduled first-call anchor has passed; show outcome does not gate this step. A dated task assigned to the credited closer and due at/after or created after the anchor counts.",
    }
    result["fu_meeting_created"] = {
        "result": ("Completed" if meeting_evidence else "Missed") if set_eligible else "Neutral",
        "support": meeting_evidence,
        "basis": "Scheduled first-call anchor has passed; show outcome does not gate this step. Any later non-canceled or non-declined meeting for the lead counts, regardless of assignee.",
    }

    window_end = anchor + timedelta(hours=24)
    messages = []
    for kind, rows in (("email", emails), ("sms", sms)):
        for msg in rows:
            at = message_stamp(msg)
            if (at and anchor <= at <= min(window_end, now)
                    and str(msg.get("direction") or "").lower() in OUTBOUND and sent(msg)
                    and (kind == "email" or (lead_owner and str(msg.get("user_id") or "") == lead_owner))):
                messages.append(event(kind, msg, at))
    recap_eligible = anchor <= now and (window_end <= now or bool(messages))
    result["recap_email"] = {"result": ("Completed" if messages else "Missed") if recap_eligible else "Neutral",
                             "support": messages, "basis": "Show outcome does not gate this step; sent outbound email, or owner-sent SMS, from anchor through the earlier of 24 hours after anchor or extract end counts; absent message is neutral until window closes."}
    status_label = str(lead.get("status_label") or "").lower()
    is_won = lead.get("status_id") == WON_STATUS_ID or "closed / won" in status_label
    is_lost = lead.get("status_id") == LOST_STATUS_ID or status_label.strip().endswith("lost")
    if is_won or is_lost:
        opportunity = next((opp for opp in lead.get("opportunities", []) if opp.get("date_won")), {})
        status_record = {"kind": "lead_status", "id": lead.get("id"),
                         "at": None, "date": opportunity.get("date_won") if is_won and opportunity else None,
                         "status": lead.get("status_label"), "user_id": None}
        basis = ("Lead is Closed/Won in the fixed Close extract; all post-call checks are exempt after a successful close."
                 if is_won else "Lead is Lost in the fixed Close extract; all post-call checks are exempt after loss.")
        for key in ("task_created", "fu_meeting_created", "recap_email"):
            result[key] = {"result": "Exempt", "support": [status_record], "basis": basis}
    return result, deadline, first


def aggregate(cells: list[dict]) -> dict:
    steps = {}
    for key, (phase, label) in STEP_LABELS.items():
        rows = [row[key]["result"] for row in cells]
        eligible = sum(v in {"Completed", "Missed"} for v in rows)
        done = sum(v == "Completed" for v in rows)
        exempt = sum(v == "Exempt" for v in rows)
        pct = floor(done / eligible * 100 + .5) if eligible else None
        steps[key] = {"phase": phase, "label": label, "done": done, "eligible": eligible, "exempt": exempt, "pct": pct}
    def mean(keys: list[str]) -> int | None:
        values = [steps[k]["pct"] for k in keys if steps[k]["pct"] is not None]
        return floor(sum(values) / len(values) + .5) if values else None
    return {"steps": steps, "pre_call_pct": mean([k for k, (p, _) in STEP_LABELS.items() if p == "Pre-call"]),
            "post_call_pct": mean([k for k, (p, _) in STEP_LABELS.items() if p == "Post-call"])}


def write_audit(extract_path: Path, preview_path: Path, dashboard_path: Path, out_dir: Path) -> dict:
    extract, preview, dashboard = (json.loads(p.read_text()) for p in (extract_path, preview_path, dashboard_path))
    year, month = map(int, extract["meta"]["month"].split("-"))
    period_start = f"{year}-{month:02d}-01"
    period_end = f"{year}-{month:02d}-{monthrange(year, month)[1]:02d}"
    now = pacific(extract["meta"]["ended_at"])
    all_dashboard_reps = dashboard["reps"]
    rep_rows = [r for r in all_dashboard_reps if not r.get("exclude_meetings") and not r.get("is_manager")]
    name_to_id = {str(name): str(uid) for uid, name in extract["users"].items()}
    visible = {name_to_id[r["name"]] for r in rep_rows if r["name"] in name_to_id}
    leads_by_rep: dict[str, list[dict]] = defaultdict(list)
    leads_by_id = {str(l["id"]): l for l in extract["leads"]}
    exclusions = Counter()
    exclusion_rows = []
    included_rows = []
    for lead in extract["leads"]:
        booked, initial_reason, _ = in_scope(lead, period_start, period_end)
        if initial_reason == "candidate" and not active_first_meeting(
            extract, str(lead["id"]), booked
        ):
            initial_reason = "inactive_booked_meeting"
        if initial_reason != "candidate":
            exclusions[initial_reason] += 1
            exclusion_rows.append({"lead_id": lead.get("id"), "lead_name": lead.get("display_name") or lead.get("name"),
                                   "reason": initial_reason, "status_id": lead.get("status_id"),
                                   "funnel": close_value(lead, "funnel"), "booked_date": booked})
            continue
        lead_owner = owner_id(lead, extract["users"])
        show_state = str(close_value(lead, "show") or "")
        deadline, first = deadline_for(booked, by_lead(extract, "meetings", str(lead["id"])), show_state)
        credited, attribution = credited_rep(first, lead_owner, visible)
        if not credited:
            exclusions[attribution] += 1
            exclusion_rows.append({"lead_id": lead.get("id"), "lead_name": lead.get("display_name") or lead.get("name"),
                "reason": attribution, "status_id": lead.get("status_id"), "funnel": close_value(lead, "funnel"),
                "booked_date": booked, "first_meeting_id": first.get("id") if first else None,
                "first_meeting_at": deadline.isoformat() if deadline else None,
                "meeting_user_id": first.get("user_id") if first else None, "current_owner_id": lead_owner})
            continue
        lead_record = {"lead": lead, "booked": booked, "owner": lead_owner, "rep": credited,
                       "attribution": attribution, "deadline": deadline, "first": first}
        included_rows.append(lead_record)
        leads_by_rep[credited].append(lead_record)

    preview_leads: dict[str, dict[str, str]] = defaultdict(dict)
    preview_lead_reps: dict[str, str] = {}
    for rep in preview.get("reps", []):
        preview_rep_id = rep.get("rep_owner_id")
        for item in rep.get("adherence", {}).get("lead_results", []):
            lead_id = str(item["id"])
            preview_lead_reps[lead_id] = str(preview_rep_id or "")
            preview_leads[lead_id].update(item.get("steps", {}))
        for key, cohorts in (rep.get("adherence", {}).get("lead_cohorts") or {}).items():
            for outcome, items in cohorts.items():
                for item in items:
                    preview_leads[str(item["id"])].setdefault(
                        key, {"completed": "Completed", "missed": "Missed", "exempt": "Exempt"}.get(outcome, outcome.title())
                    )
    # Dashboard preview cohorts omit neutral leads. Derive the rep mapping from reconciliation
    # and retain lead-level IDs solely in this ignored private audit directory.
    classifications = []
    independent_by_rep: dict[str, list[dict]] = defaultdict(list)
    preview_cells_by_rep: dict[str, list[dict]] = defaultdict(list)
    mismatch_count = 0
    attribution_mismatches = []
    unattributed_preview_ids = []
    for record in included_rows:
        lead = record["lead"]
        independent, deadline, first = classify(lead, record["rep"], record["booked"], record["owner"], extract, now)
        independent_by_rep[record["rep"]].append(independent)
        preview_cell = {key: preview_leads.get(str(lead["id"]), {}).get(key, "Neutral") for key in STEP_LABELS}
        preview_rep_id = preview_lead_reps.get(str(lead["id"]))
        if preview_rep_id is None:
            unattributed_preview_ids.append(str(lead["id"]))
        elif preview_rep_id != record["rep"]:
            attribution_mismatches.append({"lead_id": str(lead["id"]),
                "independent_rep_id": record["rep"], "preview_rep_id": preview_rep_id})
        preview_cells_by_rep[record["rep"]].append({key: {"result": value} for key, value in preview_cell.items()})
        for key, (phase, label) in STEP_LABELS.items():
            expected = independent[key]["result"]
            actual = preview_cell[key]
            same = expected == actual
            mismatch_count += not same
            support = list(independent[key]["support"])
            # The scored meeting establishes the boundary even for a neutral or missed cell.
            if first:
                support.insert(0, event("scored_first_call", first, deadline))
            explanation = "Matches independent result under approved rules." if same else (
                f"Independent result is {expected}; preview shows {actual}. "
                f"Independent basis: {independent[key]['basis']}"
            )
            classifications.append({
                "lead_id": str(lead["id"]), "lead_name": lead.get("display_name") or lead.get("name"),
                "booked_date": record["booked"], "credited_rep_id": record["rep"],
                "preview_credited_rep_id": preview_lead_reps.get(str(lead["id"])),
                "credited_rep": extract["users"].get(record["rep"], "Unknown"), "attribution": record["attribution"],
                "scored_first_call_id": first.get("id") if first else None,
                "scored_first_call_at": deadline.isoformat() if deadline else None,
                "step": key, "phase": phase, "label": label,
                "independent": expected, "preview": actual, "matches": same,
                "supporting_close_records": support, "rule_basis": independent[key]["basis"],
                "difference_explanation": explanation,
            })

    rep_summaries = {}
    aggregate_mismatches = []
    preview_reps = {r.get("rep_owner_id"): r for r in preview.get("reps", [])}
    for rep in all_dashboard_reps:
        rid = name_to_id.get(rep["name"])
        independent = aggregate(independent_by_rep.get(rid, []))
        preview_aggregate = (preview_reps.get(rid) or {}).get("adherence", {})
        preview_summary = {"pre_call_pct": preview_aggregate.get("pre_call_pct"),
            "post_call_pct": preview_aggregate.get("post_call_pct"), "steps": preview_aggregate.get("steps", {})}
        for key, expected_step in independent["steps"].items():
            actual_step = preview_summary["steps"].get(key, {})
            for field in ("done", "eligible", "exempt", "pct"):
                if actual_step.get(field) != expected_step[field]:
                    aggregate_mismatches.append({"rep": rep["name"], "step": key, "field": field,
                        "independent": expected_step[field], "preview": actual_step.get(field)})
        for field in ("pre_call_pct", "post_call_pct"):
            if preview_summary[field] != independent[field]:
                aggregate_mismatches.append({"rep": rep["name"], "field": field,
                    "independent": independent[field], "preview": preview_summary[field]})
        rep_summaries[rep["name"]] = {"credited_leads": len(leads_by_rep.get(rid, [])),
            "independent": independent, "preview": preview_summary}
    total_cells = len(classifications)
    status_counts = Counter(row["independent"] for row in classifications)
    extract_end = pacific(extract["meta"]["ended_at"])
    included_statuses = Counter(str(record["lead"].get("status_label") or "") for record in included_rows)
    attribution_counts = Counter(record["attribution"] for record in included_rows)
    owner_mismatch_count = sum(bool(record["owner"] and record["owner"] != record["rep"]) for record in included_rows)
    missing_meeting_count = sum(record["first"] is None for record in included_rows)
    upcoming_first_call_count = sum(bool(record["deadline"] and record["deadline"] > extract_end) for record in included_rows)
    unknown_show_count = sum(str(close_value(record["lead"], "show") or "").strip().lower() not in {"yes", "no"}
                             for record in included_rows)
    lost_count = sum("lost" in str(record["lead"].get("status_label") or "").lower() for record in included_rows)
    result = {
        "meta": {"month": extract["meta"]["month"], "timezone": extract["meta"]["timezone"],
                 "extract_started_at": extract["meta"]["started_at"], "extract_ended_at": extract["meta"]["ended_at"],
                 "independent_method": "scripts/audit_adherence_extract.py; separate implementation of docs/designs/process-adherence-drawer.md",
                 "raw_leads": len(extract["leads"]), "included_leads": len(included_rows),
                 "excluded_leads": len(exclusion_rows), "lead_step_cells_reviewed": total_cells,
                 "expected_lead_step_cells": len(included_rows) * len(STEP_LABELS),
                 "cell_results": dict(status_counts), "discrepancy_cells": mismatch_count,
                 "lead_attribution_mismatches": len(attribution_mismatches),
                 "preview_leads_missing_rep_assignment": len(unattributed_preview_ids),
                 "aggregate_mismatches": len(aggregate_mismatches),
                 "exclusion_counts": dict(exclusions), "activity_counts": extract["meta"]["activity_counts"],
                 "task_count": extract["meta"]["task_count"], "api_requests": extract["meta"]["api_requests"],
                 "included_statuses": dict(included_statuses), "attribution_counts": dict(attribution_counts),
                 "leads_without_same_day_meeting": missing_meeting_count,
                 "first_calls_upcoming_at_extract_end": upcoming_first_call_count,
                 "unknown_show_outcome": unknown_show_count, "included_lost_leads": lost_count,
                 "credited_rep_differs_from_current_owner": owner_mismatch_count,
                 "source_limitations": [
                     "Close meeting assignment identifies a credited rep but does not prove attendance; the score uses the lead's First Call Show Up (Opp) field.",
                     "Close does not provide historical lead-owner assignment in this extract; timed SMS ownership checks use the current Lead Owner.",
                     "One included lead has no valid same-day meeting; its recorded booked date is used as the end-of-day timing fallback.",
                     "The extract is a fixed current-state snapshot; it cannot reconstruct status, ownership, meeting, or task changes after the extract ended.",
                 ]},
        "reps": rep_summaries,
        "exclusions": exclusion_rows,
        "attribution_mismatches": attribution_mismatches,
        "preview_leads_missing_rep_assignment": unattributed_preview_ids,
        "aggregate_mismatches": aggregate_mismatches,
        "lead_by_step": classifications,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "reconciliation.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    with (out_dir / "lead-by-step.csv").open("w", newline="") as stream:
        fields = ["lead_id", "lead_name", "booked_date", "credited_rep", "credited_rep_id", "attribution",
                  "scored_first_call_id", "scored_first_call_at", "step", "phase", "label", "independent",
                  "preview", "matches", "supporting_close_records", "rule_basis", "difference_explanation"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in classifications:
            item = {**row, "supporting_close_records": json.dumps(row["supporting_close_records"], sort_keys=True)}
            writer.writerow({key: item[key] for key in fields})
    summary = [
        f"# Private Process Adherence Reconciliation — {result['meta']['month']}", "",
        f"Close extract window: {result['meta']['extract_started_at']} to {result['meta']['extract_ended_at']} ({result['meta']['timezone']}).",
        f"Coverage: {result['meta']['raw_leads']} raw leads; {result['meta']['included_leads']} included; "
        f"{result['meta']['excluded_leads']} excluded; {result['meta']['lead_step_cells_reviewed']} of "
        f"{result['meta']['expected_lead_step_cells']} lead-step cells reviewed.",
        f"Result cells: {result['meta']['cell_results']}. Cell differences: {result['meta']['discrepancy_cells']}; "
        f"attribution differences: {result['meta']['lead_attribution_mismatches']}; aggregate differences: "
        f"{result['meta']['aggregate_mismatches']}.",
        f"Close reads: {result['meta']['api_requests']} API requests; {sum(result['meta']['activity_counts'].values())} "
        f"activities across all captured kinds; {result['meta']['task_count']} tasks.", "",
        "## Rep recalculation", "",
        "| Rep | Leads | Pre-call | Post-call | " + " | ".join(STEP_LABELS) + " |",
        "| --- | ---: | ---: | ---: | " + " | ".join(["---:"] * len(STEP_LABELS)) + " |",
    ]
    for name, rep in result["reps"].items():
        independent = rep["independent"]
        phase_cell = lambda value: "—" if value is None else f"{value}%"
        step_cells = [
            f"{cell['done']}/{cell['eligible']} ({phase_cell(cell['pct'])})"
            for cell in independent["steps"].values()
        ]
        summary.append(f"| {name} | {rep['credited_leads']} | {phase_cell(independent['pre_call_pct'])} | "
                       f"{phase_cell(independent['post_call_pct'])} | " + " | ".join(step_cells) + " |")
    summary.extend(["", "## Excluded leads", ""])
    if exclusion_rows:
        summary.extend(["| Lead | Close lead ID | Reason |", "| --- | --- | --- |"])
        summary.extend(f"| {row.get('lead_name') or 'Unnamed'} | {row['lead_id']} | {row['reason']} |" for row in exclusion_rows)
    else:
        summary.append("None.")
    summary.extend(["", "## Source limits", ""])
    summary.extend(f"- {item}" for item in result["meta"]["source_limitations"])
    summary.extend(["", "## Difference review", "", "Every lead-step row is in `lead-by-step.csv` and `reconciliation.json`. "
                    "Each row includes independent result, preview result, scored first-call evidence, rule basis, "
                    "supporting Close activity IDs/timestamps, and a difference explanation. Exclusions with "
                    "their Close-record basis are listed above."])
    (out_dir / "summary.md").write_text("\n".join(summary) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--dashboard", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = write_audit(args.extract, args.preview, args.dashboard, args.output_dir)
    print(json.dumps(result["meta"], indent=2))
    print("Rep step/phase summary:")
    for name, values in result["reps"].items():
        independent = values["independent"]
        print(f"  {name}: leads={values['credited_leads']} pre={independent['pre_call_pct']} post={independent['post_call_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
