"""Aggregate drilldowns over the accepted V2 records; no booking selection here."""

from collections import defaultdict

SCHEMA_VERSION = 3
BOOKING_SCOPE = "vp-closers.v1"
VP_BOOKING_CLOSERS = (
    "Christian Hartwell", "Scott Seymour", "Robin Perkins", "Eric Piccione",
    "Joe Vaughan", "Shreya Bechra", "Luke Herman", "Ariella Irvine", "Joe Dysert",
)
CLOSERS = (
    "Christian Hartwell", "Scott Seymour", "Robin Perkins", "Eric Piccione",
    "Joe Vaughan", "Shreya Bechra", "Luke Herman", "Ariella Irvine",
    "Oscar Pugh", "Joe Dysert",
)
OTHER_OWNER = "Other / Non-Closer"
METRICS = ("bookings", "marketing", "lane2", "social_dms", "showed", "qualified", "closed_won", "revenue")


def owner_name(value, users):
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("id") or value.get("name")
    value = " ".join(str(value or "").split())
    # Unresolved user IDs never appear in public reports.
    return users.get(value, "Unassigned" if value.startswith("user_") or not value else value)


def vp_closer_bookings(bookings, users):
    """Apply sales roster scope after the complete-history booking decisions."""
    return [row for row in bookings if owner_name(row.get("owner"), users) in VP_BOOKING_CLOSERS]


def rollup(bookings, won, key):
    groups = defaultdict(lambda: {**dict.fromkeys(METRICS, 0), "qualified_ids": set()})
    for row in bookings:
        group = groups[key(row)]
        group["bookings"] += 1
        group[{"Marketing": "marketing", "Lane 2": "lane2", "Social DMs": "social_dms"}[row["bucket"]]] += 1
        group["showed"] += int(row["showed"])
        if row["qualified"]:
            group["qualified_ids"].add(row["lead_id"])
    for row in won:
        group = groups[key(row)]
        group["closed_won"] += 1
        group["revenue"] += row["revenue"]
    result = []
    for value, group in groups.items():
        group["qualified"] = len(group.pop("qualified_ids"))
        result.append({"value": value, **group})
    return sorted(result, key=lambda row: (-row["bookings"], -row["revenue"], row["value"]))


def build_breakdowns(bookings, won, users):
    channels = sorted({row["channel"] for row in bookings + won})
    details = {}
    for channel in channels:
        booked = [row for row in bookings if row["channel"] == channel]
        closed = [row for row in won if row["channel"] == channel]
        field = "utm_content" if channel == "Internal Webinar" else "utm_campaign"
        details[channel] = {
            "drilldowns": rollup(booked, closed, lambda row: row[field]),
            "packages": rollup([], closed, lambda row: row["package"]),
            "setters": rollup(booked, closed, lambda row: owner_name(row["setter"], users)),
        }
    setters = rollup(bookings, won, lambda row: owner_name(row["setter"], users))
    team = rollup(bookings, won, lambda row: owner_name(row["owner"], users)
                  if owner_name(row["owner"], users) in CLOSERS else OTHER_OWNER)
    return {"channels": details, "setters": setters, "team": team}
