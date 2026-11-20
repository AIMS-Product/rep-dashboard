"""Read month-to-date Avoma scorecard averages for dashboard reps."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
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


def fetch_avoma_mtd_scores(api_key: str, *, as_of=None, audit_path=None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return per-rep mean overall_score and call count for local-calendar MTD.

    Avoma's date filters select scorecard evaluation records. The meeting's
    start_at is checked again in Pacific time so MTD follows the dashboard's
    local reporting calendar. Results are keyed by scored_for.email.
    """
    if not api_key:
        print("  AVOMA_API_KEY is not set; Avoma MTD scores will be blank", flush=True)
        return {}, {"available": False, "reason": "missing_api_key"}

    local_now = as_of.astimezone(PACIFIC) if as_of else datetime.now(PACIFIC)
    local_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    utc_start = local_start.astimezone(timezone.utc)
    utc_now = local_now.astimezone(timezone.utc)
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

    # One score per rep per call. Keep the most recently modified evaluation
    # if Avoma ever returns duplicate evaluations for a rep and meeting.
    if audit_path is not None:
        audit_path.write_text(json.dumps({"as_of":local_now.isoformat(),"evaluations":evaluations}));audit_path.chmod(0o600)
    latest_by_rep_meeting: dict[tuple[str, str], dict[str, Any]] = {}
    for evaluation in evaluations:
        if evaluation.get("scorecard_template_name") != SCORECARD_TEMPLATE:
            continue
        meeting = evaluation.get("meeting") or {}
        try:
            meeting_start = datetime.fromisoformat(
                str(meeting.get("start_at") or "").replace("Z", "+00:00")
            ).astimezone(PACIFIC)
        except ValueError:
            continue
        if not local_start <= meeting_start <= local_now:
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
        "period_end": local_now.date().isoformat(),
        "scale_max": 5,
        "evaluation_count": sum(item["count"] for item in scores.values()),
        "generated_at": local_now.isoformat(timespec="seconds"),
    }
    print(
        f"  Avoma MTD: {meta['evaluation_count']} scored calls across {len(scores)} reps "
        f"({meta['period_start']} through {meta['period_end']}, Pacific)",
        flush=True,
    )
    return scores, meta
