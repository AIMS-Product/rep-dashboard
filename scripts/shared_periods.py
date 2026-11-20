"""Pacific reporting windows for the joint sales release."""

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
WEEK = timedelta(days=7)


def week_starts(cutoff: datetime) -> tuple[date, date]:
    today = cutoff.astimezone(PACIFIC).date()
    monday = today - timedelta(days=today.weekday())
    return monday, monday - WEEK


def week_metadata(start: date, cutoff: datetime) -> dict:
    end = start + WEEK
    scheduled_end = datetime.combine(end, time.min, PACIFIC)
    return {
        "key": start.isoformat(),
        "kind": "week",
        "start": start.isoformat(),
        "end": (end - timedelta(days=1)).isoformat(),
        "end_exclusive": min(cutoff, scheduled_end).isoformat(),
        "complete": cutoff >= scheduled_end,
    }


def scheduled_between(scheduled: dict, start: date, end: datetime) -> dict:
    lower = datetime.combine(start, time.min, PACIFIC)
    rows = [row for row in scheduled["rows"]
            if lower <= datetime.fromisoformat(row["starts_at"]) < end]
    return {**scheduled, "rows": rows, "period_start": start.isoformat()}


def weekly_canonical(period, scheduled, occurred, opportunities, *, user_map, roster, cutoff, rules_version):
    """Project Manager's common metrics from the same accepted call and won identities."""
    rows = occurred["rows"]
    start = period["start"]
    end = (datetime.fromisoformat(period["end_exclusive"]) - timedelta(microseconds=1)).date().isoformat()
    wins = [row for row in opportunities if start <= row["date_won"][:10] <= end]
    owners = defaultdict(Counter)
    for row in scheduled["rows"]:
        owners[row["owner"]]["scheduled_calls"] += 1
    for row in rows:
        counts = owners[row["owner"]]
        counts["occurred_calls"] += 1
        counts["showed"] += int(row["occurred_kind"] == "show")
        counts["no_shows"] += int(row["occurred_kind"] == "no_show")
        counts["qualified"] += row["qualified_contribution"]
    for row in wins:
        counts = owners[user_map.get(row["user_id"], "Other / Non-Closer")]
        counts["closed_won"] += 1
        counts["revenue"] += row["reporting_revenue"]

    team = []
    for name, counts in sorted(owners.items()):
        counts["unresolved_calls"] = counts["scheduled_calls"] - counts["occurred_calls"]
        team.append({"value": name, **counts})
    channels = Counter(row["channel"] for row in rows)
    return {
        "period": period,
        "cutoff": cutoff.isoformat(),
        "rules_version": rules_version,
        "roster": roster,
        "user_map": user_map,
        "scheduled_rows": scheduled["rows"],
        "occurred_rows": rows,
        "opportunities": wins,
        "mtd": {
            "source_cutoff": cutoff.isoformat(),
            "rules_version": rules_version,
            "booking_scope": scheduled["scope"],
            "totals": {
                **occurred["totals"], "closed_won": len(wins),
                "revenue": sum(row["reporting_revenue"] for row in wins),
            },
            "team": team,
            "channels": [{"channel": channel, "occurred_calls": count} for channel, count in channels.most_common()],
        },
    }
