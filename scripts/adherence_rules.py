"""Pure process-adherence rules for the local rep-dashboard baseline.

The module deliberately knows nothing about HTTP or files.  It accepts Close-shaped dictionaries
and returns only boolean evidence plus aggregate counts, which keeps the rules unit-testable and
makes the later SteelTrap data-source swap small.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from math import floor
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo


PACIFIC = ZoneInfo("America/Los_Angeles")
CANCELED_MEETING_STATUSES = {"canceled", "declined-by-lead", "declined-by-org"}
OUTBOUND_DIRECTIONS = {"outbound", "outgoing"}
SENT_ACTIVITY_WEBHOOK_ROLLOUT_AT = datetime(2026, 7, 23, 18, 42, 25, tzinfo=ZoneInfo("UTC"))
LOOM_PATTERN = re.compile(r"(^|[^a-z])loom\.com", re.IGNORECASE)

STEP_META = {
    "loom_usage": {"phase": "pre_call", "label": "Pre-Call Loom"},
    "precall_text": {"phase": "pre_call", "label": "Pre-call text"},
    "followup_task": {"phase": "post_call", "label": "Next steps set"},
    "followup_completed": {"phase": "post_call", "label": "Next steps completed"},
    "recap_email": {"phase": "post_call", "label": "Post-call follow-up"},
}


def parse_datetime(value: Any, *, date_at_end_of_day: bool = False) -> datetime | None:
    """Parse Close timestamps and date-only values into timezone-aware datetimes."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max if date_at_end_of_day else time.min)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            if len(raw) == 10:
                parsed_date = date.fromisoformat(raw)
                parsed = datetime.combine(
                    parsed_date,
                    time.max if date_at_end_of_day else time.min,
                )
            else:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PACIFIC)
    return parsed.astimezone(PACIFIC)


def activity_time(activity: dict[str, Any]) -> datetime | None:
    """Return the evidence time used by SteelTrap-style rules."""
    for field in ("activity_at", "date_sent", "date_created"):
        parsed = parse_datetime(activity.get(field))
        if parsed:
            return parsed
    return None


def activity_body(activity: dict[str, Any]) -> str:
    """Normalize only message/note bodies; subjects and titles are not process evidence."""
    fields = ("body_text", "text", "note", "body_html", "note_html")
    return "\n".join(str(activity.get(field) or "") for field in fields).strip()


def is_outbound(activity: dict[str, Any]) -> bool:
    return str(activity.get("direction") or "").strip().lower() in OUTBOUND_DIRECTIONS


def is_active_activity(activity: dict[str, Any]) -> bool:
    return str(activity.get("status") or "").strip().lower() not in {"deleted", "archived"}


def is_sent_activity(activity: dict[str, Any]) -> bool:
    """Mirror SteelTrap's sent-status gate, including its pre-webhook legacy allowance."""
    if not is_active_activity(activity):
        return False
    status = str(activity.get("status") or "").strip().lower()
    if status in {"sent", "completed"}:
        return True
    stamp = activity_time(activity)
    return bool(
        stamp
        and stamp.astimezone(ZoneInfo("UTC")) < SENT_ACTIVITY_WEBHOOK_ROLLOUT_AT
        and status in {"", "draft", "outbox"}
    )


def meeting_is_assigned_to(meeting: dict[str, Any], owner_id: str | None) -> bool:
    """Match the current owner, while preserving SteelTrap's unassigned allowance."""
    assigned = {str(value) for value in (meeting.get("users") or []) if value}
    if meeting.get("user_id"):
        assigned.add(str(meeting["user_id"]))
    if not assigned:
        return True
    return bool(owner_id and owner_id in assigned)


def task_is_assigned_to(task: dict[str, Any], owner_id: str | None) -> bool:
    assigned = task.get("assigned_to")
    return not assigned or bool(owner_id and str(assigned) == owner_id)


def first_call_deadline(
    booked_date: str,
    meetings: Iterable[dict[str, Any]],
) -> tuple[datetime | None, dict[str, Any] | None]:
    """Use the earliest valid same-day meeting, then fall back to booked-date end-of-day."""
    booked = parse_datetime(booked_date, date_at_end_of_day=True)
    if not booked:
        return None, None
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for meeting in meetings:
        if str(meeting.get("status") or "").lower() in CANCELED_MEETING_STATUSES:
            continue
        starts_at = parse_datetime(meeting.get("starts_at") or meeting.get("activity_at"))
        if starts_at and starts_at.date() == booked.date():
            candidates.append((starts_at, meeting))
    if candidates:
        starts_at, meeting = min(candidates, key=lambda pair: pair[0])
        return starts_at, meeting
    return booked, None


def _qualifying_tasks(tasks: Iterable[dict[str, Any]], owner_id: str | None) -> list[dict[str, Any]]:
    return [
        task
        for task in tasks
        if task.get("lead_id")
        and task.get("date")
        and task_is_assigned_to(task, owner_id)
        and str(task.get("_type") or "lead") == "lead"
    ]


def _completion_time(activity: dict[str, Any]) -> datetime | None:
    return activity_time(activity)


def score_lead(
    *,
    booked_date: str,
    show_state: str,
    owner_id: str | None,
    emails: Iterable[dict[str, Any]] = (),
    sms: Iterable[dict[str, Any]] = (),
    notes: Iterable[dict[str, Any]] = (),
    meetings: Iterable[dict[str, Any]] = (),
    tasks: Iterable[dict[str, Any]] = (),
    task_completions: Iterable[dict[str, Any]] = (),
    now: datetime | None = None,
) -> dict[str, dict[str, bool]]:
    """Score one monthly-cohort lead against the five adherence signals."""
    now = (now or datetime.now(PACIFIC)).astimezone(PACIFIC)
    meetings = list(meetings)
    deadline, first_meeting = first_call_deadline(booked_date, meetings)
    result = {key: {"eligible": False, "done": False} for key in STEP_META}
    if not deadline:
        return result

    pre_eligible = deadline <= now
    comms = [*emails, *sms, *notes]
    loom_done = any(
        (stamp := activity_time(activity)) is not None
        and stamp <= deadline
        and is_active_activity(activity)
        and bool(LOOM_PATTERN.search(activity_body(activity)))
        for activity in comms
    )
    precall_text_done = any(
        (stamp := activity_time(message)) is not None
        and stamp <= deadline
        and is_outbound(message)
        and is_sent_activity(message)
        and bool(activity_body(message))
        for message in sms
    )
    result["loom_usage"] = {"eligible": pre_eligible, "done": pre_eligible and loom_done}
    result["precall_text"] = {
        "eligible": pre_eligible,
        "done": pre_eligible and precall_text_done,
    }

    normalized_show = str(show_state or "").strip().lower()
    outcome_known = normalized_show in {"yes", "no"}
    shown = normalized_show == "yes"
    anchor = deadline
    if shown and first_meeting:
        anchor = parse_datetime(first_meeting.get("starts_at") or first_meeting.get("activity_at")) or deadline

    set_eligible = outcome_known and anchor <= now
    qualified_tasks = _qualifying_tasks(tasks, owner_id)
    later_meetings = []
    for meeting in meetings:
        starts_at = parse_datetime(meeting.get("starts_at") or meeting.get("activity_at"))
        if not starts_at or starts_at <= anchor:
            continue
        if str(meeting.get("status") or "").lower() in CANCELED_MEETING_STATUSES:
            continue
        if not meeting_is_assigned_to(meeting, owner_id):
            continue
        later_meetings.append(meeting)

    result["followup_task"] = {
        "eligible": set_eligible,
        "done": set_eligible and bool(qualified_tasks or later_meetings),
    }

    completion_eligible = shown and anchor <= now
    qualifying_task_ids = {task.get("id") for task in qualified_tasks if task.get("id")}
    completed_task = any(
        completion.get("task_id") in qualifying_task_ids
        and (stamp := _completion_time(completion)) is not None
        and stamp >= anchor
        for completion in task_completions
    )
    completed_meeting = any(
        str(meeting.get("status") or "").lower() == "completed"
        for meeting in later_meetings
    )
    result["followup_completed"] = {
        "eligible": completion_eligible,
        "done": completion_eligible and (completed_task or completed_meeting),
    }

    window_end = anchor + timedelta(hours=24)
    post_call_message = any(
        (stamp := activity_time(message)) is not None
        and anchor <= stamp <= window_end
        and is_outbound(message)
        and is_sent_activity(message)
        for message in [*emails, *sms]
    )
    result["recap_email"] = {
        "eligible": completion_eligible,
        "done": completion_eligible and post_call_message,
    }
    return result


def round_percent(numerator: int, denominator: int) -> int | None:
    if denominator <= 0:
        return None
    return floor((numerator / denominator * 100) + 0.5)


def round_mean(values: Iterable[int | None]) -> int | None:
    available = [value for value in values if value is not None]
    if not available:
        return None
    return floor((sum(available) / len(available)) + 0.5)


def aggregate_rep_scores(
    evidence_by_rep: dict[str, list[dict[str, dict[str, bool]]]],
) -> dict[str, dict[str, Any]]:
    """Aggregate lead evidence into the stable rep-level JSON contract."""
    output: dict[str, dict[str, Any]] = {}
    for rep_id, lead_rows in evidence_by_rep.items():
        counts = defaultdict(lambda: {"done": 0, "eligible": 0})
        for row in lead_rows:
            for key in STEP_META:
                cell = row.get(key) or {}
                if cell.get("eligible"):
                    counts[key]["eligible"] += 1
                    if cell.get("done"):
                        counts[key]["done"] += 1
        steps = {}
        for key, meta in STEP_META.items():
            done = counts[key]["done"]
            eligible = counts[key]["eligible"]
            steps[key] = {
                "label": meta["label"],
                "phase": meta["phase"],
                "done": done,
                "eligible": eligible,
                "pct": round_percent(done, eligible),
            }
        output[rep_id] = {
            "pre_call_pct": round_mean(
                [steps["loom_usage"]["pct"], steps["precall_text"]["pct"]]
            ),
            "post_call_pct": round_mean(
                [
                    steps["followup_task"]["pct"],
                    steps["followup_completed"]["pct"],
                    steps["recap_email"]["pct"],
                ]
            ),
            "steps": steps,
        }
    return output


def validate_aggregate(adherence: dict[str, Any]) -> None:
    for phase_key in ("pre_call_pct", "post_call_pct"):
        value = adherence.get(phase_key)
        if value is not None and not 0 <= value <= 100:
            raise ValueError(f"{phase_key} outside 0..100: {value}")
    for key, cell in (adherence.get("steps") or {}).items():
        done = int(cell.get("done", 0))
        eligible = int(cell.get("eligible", 0))
        pct = cell.get("pct")
        if not 0 <= done <= eligible:
            raise ValueError(f"invalid counts for {key}: {done}/{eligible}")
        if pct is not None and not 0 <= pct <= 100:
            raise ValueError(f"invalid percentage for {key}: {pct}")
