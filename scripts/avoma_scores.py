"""Read month-to-date Avoma scorecard averages for dashboard reps."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


AVOMA_API_BASE = "https://api.avoma.com/v1"
PACIFIC = ZoneInfo("America/Los_Angeles")
SCORECARD_TEMPLATE = "VP Closing Call Scorecard"


# Verified Close login aliases for the active VP reps' Avoma accounts.
AVOMA_EMAIL_ALIASES = {
    f"{name}@vendingpreneurshq.com": f"{name}@modern-amenities.com"
    for name in ("ariella", "christian", "eric", "luke", "robin", "scott")
}


def score_for_close_email(scores, email):
    email = str(email or "").strip().casefold()
    if email in scores:
        return scores[email]
    return scores.get(AVOMA_EMAIL_ALIASES.get(email, ""), {})


def fetch_avoma_mtd_scores(api_key: str, *, as_of=None, audit_path=None):
    """Return per-rep mean and call count for local-calendar MTD."""
    cutoff = as_of.astimezone(PACIFIC) if as_of else datetime.now(PACIFIC)
    start = cutoff.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    evaluations = fetch_avoma_evaluations(api_key, start, cutoff, audit_path=audit_path)
    return summarize_avoma_scores(evaluations, start, cutoff, cutoff)


def fetch_avoma_evaluations(api_key, local_start, cutoff, *, audit_path=None):
    """Fetch evaluations once for all reporting windows in the release."""
    if not api_key:
        return None
    utc_start = local_start.astimezone(timezone.utc)
    utc_now = cutoff.astimezone(timezone.utc)
    params = urlencode({
        "from_date": utc_start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "to_date": utc_now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "page_size": 100,
    })
    url = f"{AVOMA_API_BASE}/scorecard_evaluations/?{params}"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    evaluations: list[dict[str, Any]] = []

    while url:
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode())
        except HTTPError as error:
            raise RuntimeError(f"Avoma scorecard API returned HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise RuntimeError("Could not reach the Avoma scorecard API") from error
        if not isinstance(payload, dict):
            raise RuntimeError("Avoma scorecard API returned an unexpected response")
        evaluations.extend(payload.get("results") or [])
        url = payload.get("next")

    if audit_path is not None:
        audit_path.write_text(json.dumps({"as_of": cutoff.isoformat(), "evaluations": evaluations}))
        audit_path.chmod(0o600)
    return evaluations


def summarize_avoma_scores(evaluations, local_start, end_exclusive, cutoff):
    if evaluations is None:
        return {}, {"available": False, "reason": "missing_api_key"}

    start_utc = local_start.astimezone(timezone.utc)
    end_utc = end_exclusive.astimezone(timezone.utc)
    # One score per rep per call, using the last revision of the evaluation.
    latest_by_rep_meeting: dict[tuple[str, str], dict[str, Any]] = {}
    for evaluation in evaluations:
        if evaluation.get("scorecard_template_name") != SCORECARD_TEMPLATE:
            continue
        meeting = evaluation.get("meeting") or {}
        try:
            meeting_start = datetime.fromisoformat(
                str(meeting.get("start_at") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError:
            continue
        if not start_utc <= meeting_start < end_utc:
            continue

        scored_for = evaluation.get("scored_for") or {}
        email = str(scored_for.get("email") or "").strip().casefold()
        meeting_uuid = str(meeting.get("uuid") or "").strip()
        score = evaluation.get("overall_score")
        if not email or not meeting_uuid or isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        key = (email, meeting_uuid)
        previous = latest_by_rep_meeting.get(key)
        if previous is None or str(evaluation.get("modified") or "") > str(previous.get("modified") or ""):
            latest_by_rep_meeting[key] = evaluation

    scores_by_email: dict[str, list[float]] = defaultdict(list)
    for (email, _meeting_uuid), evaluation in latest_by_rep_meeting.items():
        scores_by_email[email].append(float(evaluation["overall_score"]))

    scores = {
        email: {"score": sum(values) / len(values), "count": len(values)}
        for email, values in scores_by_email.items()
    }
    meta = {
        "available": True,
        "source": "Avoma scorecard evaluations",
        "scorecard": SCORECARD_TEMPLATE,
        "timezone": "America/Los_Angeles",
        "period_start": local_start.date().isoformat(),
        "period_end": (end_exclusive - timedelta(microseconds=1)).date().isoformat(),
        "scale_max": 5,
        "evaluation_count": sum(item["count"] for item in scores.values()),
        "generated_at": cutoff.isoformat(timespec="seconds"),
    }
    print(
        f"  Avoma QA: {meta['evaluation_count']} scored calls across {len(scores)} reps "
        f"({meta['period_start']} through {meta['period_end']}, Pacific)",
        flush=True,
    )
    return scores, meta
