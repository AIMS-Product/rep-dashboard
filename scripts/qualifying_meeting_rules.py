"""Pinned exact meeting classifier from close-first-sales-meeting/update_field.py.

Generated from the updater source; change this file only by regenerating from that
source and verify SOURCE_SHA256 in the local workspace tests.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
CANCELED_PREFIXES = ("canceled", "cancelled", "declined")

SOURCE_SHA256 = '23497b31532690c6604281c94423a2265bd25ef0d8e1d3fa2d274a3de528cb19'
QUALIFYING_TIERS = frozenset({'vendhub_nextsteps', 'scraper', 'post_webinar', 'quick_discovery', 'vendhub_consultation', 'react_email', 'closer'})

EXCLUDED_OWNERS = {
    "user_5cZRqXu8kb4O1IeBVA98UMcMEhYZUhx1fnCHfSL0YMV",  # Stephen Olivas
    "user_yRF070m26JE67J6CJqzkAB3IqY7btNm1K5RisCglKa6",  # Ahmad Bukhari
}

SETTER_OWNERS = {
    "user_EmhqCmaHERTfgfWnPADiLGEqQw3ENvRYd3u1VEmblIp",  # Kristin Nelson
    "user_4sfuKGMbv0LQZ4hpS8ipASv406kKTSNP5Xx79jOwSqM",  # Spencer Reynolds
}

RE_CANCELED          = re.compile(r"^canceled[\s:]?", re.IGNORECASE)

RE_FOLLOWUP          = re.compile(r"follow[\-\s]?up|fallow\s+up|f/u\b|next\s+steps|reschedul", re.IGNORECASE)

RE_ENROLLMENT        = re.compile(r"enrollment|silver\s+start\s*up|bronze\s+enrollment|questions\s+on\s+enrollment", re.IGNORECASE)

RE_DISCOVERY_TITLE   = re.compile(r"vending\s+quick\s+discovery", re.IGNORECASE)

RE_POSTWEBINAR_TITLE    = re.compile(r"post\s+masterclass\s+strategy\s+call", re.IGNORECASE)

RE_ROUTE_PLANNING_TITLE   = re.compile(r"route\s+planning\s+call", re.IGNORECASE)

RE_REACT_EMAIL_TITLE      = re.compile(r"vending\s+consultation\s+&\s+strategy\s+session", re.IGNORECASE)

RE_QUICK_DISC_TITLE       = re.compile(r"vending\s+consult\s+call", re.IGNORECASE)

RE_VENDHUB_CONSULTATION   = re.compile(r"vendhub\s+consultation\s+call", re.IGNORECASE)

RE_VENDHUB_NEXTSTEPS      = re.compile(r"vendhub\s+next\s+steps\s+call", re.IGNORECASE)

SCRAPER_TITLE_MAP = [
    (re.compile(r"vendingpren[eu]+rs?\s+pinnacle\s*-\s*next\s+steps", re.IGNORECASE), "Monde"),  # Vendingpreneurs Pinnacle - Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+executive\s*-\s*next\s+steps", re.IGNORECASE), "Finley Eastlake"),  # Vendingpreneurs Executive - Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+-\s+next\s+steps\s+call", re.IGNORECASE),     "Charlie Ingram"),  # Vendingpreneurs - Next Steps Call (Jennifer replaced Kristin Nelson)

    (re.compile(r"vendingpren[eu]+rs?\s+next\s+steps\s+call", re.IGNORECASE),         "Vince Bartolini"),   # Vendingpreneurs Next Steps Call
    (re.compile(r"vendingpren[eu]+r\s+next\s+steps", re.IGNORECASE),                  "William Nowak"),     # Vendingpreneur Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+next\s+steps\s+session", re.IGNORECASE),      "Pearl Sathekge"),       # Vendingpreneurs Next Steps Session


    (re.compile(r"vending\s+discovery\s+call\s+-\s+next\s+steps", re.IGNORECASE),    "August Young"),      # Vending Discovery Call - Next Steps


    (re.compile(r"vending\s+opportunity\s*-?\s*next\s+steps", re.IGNORECASE),          "Cassie Caraballo"),  # Vending Opportunity - Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+connect\s*-?\s*next\s+steps", re.IGNORECASE),  "Jessica Zatkin"),    # Vendingpreneurs Connect - Next Steps

    (re.compile(r"vendingpren[eu]+rs?\s+momentum\s*-?\s*next\s+steps", re.IGNORECASE), "Connor George"),  # Vendingpreneurs Momentum - Next Steps

    (re.compile(r"vendingpren[eu]+rs?\s+pathway\s*-?\s*next\s+steps", re.IGNORECASE), "Naria Torres"),  # Vendingpreneurs Pathway - Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+blueprint\s*-?\s*next\s+steps", re.IGNORECASE), "Melia King"),  # Vendingpreneurs Blueprint - Next Steps
    (re.compile(r"tlg\s+pinnacle\s*-?\s*next\s+steps", re.IGNORECASE), "Kaylane Nunes"),  # TLG Pinnacle - Next Steps
    (re.compile(r"tlg\s+compass\s*-?\s*next\s+steps", re.IGNORECASE), "Josh Stoffel"),  # TLG Compass - Next Steps

    (re.compile(r"tlg\s+elevate\s*-?\s*next\s+steps", re.IGNORECASE), "Catalina"),  # TLG Elevate - Next Steps


    (re.compile(r"tlg\s+clarity\s*-?\s*next\s+steps", re.IGNORECASE), "Luna"),  # TLG Clarity - Next Steps
    (re.compile(r"tlg\s+vanguard\s*-?\s*next\s+steps", re.IGNORECASE), "Connor Mason"),  # TLG Vanguard - Next Steps
    (re.compile(r"tlg\s+northstar\s*-?\s*next\s+steps", re.IGNORECASE), "Jonathan Quinn"),  # TLG Northstar - Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+ascent\s*-?\s*next\s+steps", re.IGNORECASE), "Owen Hart"),  # Vendingpreneurs Ascent - Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+keystone\s*-?\s*next\s+steps", re.IGNORECASE), "Brad Savage"),  # Vendingpreneurs Keystone - Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+summit\s*-?\s*next\s+steps", re.IGNORECASE), "Rob Maxfield"),  # Vendingpreneurs Summit - Next Steps
    (re.compile(r"vendingpren[eu]+rs?\s+growth\s*-?\s*next\s+steps", re.IGNORECASE), "Igor Trojanowski"),  # Vendingpreneurs Growth - Next Steps
]

CLOSER_PATTERNS = [
    re.compile(r"vending\s+strategy\s+call", re.IGNORECASE),
    re.compile(r"vendingpren[eu]+rs?\s+consultation", re.IGNORECASE),
    re.compile(r"vendingpren[eu]+rs?\s+strategy\s+call", re.IGNORECASE),
    re.compile(r"new\s+vendingpren[eu]+r\s+strategy\s+call", re.IGNORECASE),
    re.compile(r"vending\s+consult\b", re.IGNORECASE),
    re.compile(r"post\s+masterclass\s+strategy\s+call", re.IGNORECASE),
    re.compile(r"vending\s+route\s+consultation", re.IGNORECASE),
    re.compile(r"cash[\-\s]flowing\s+vending\s+route\s+advisory\s+interview", re.IGNORECASE),
    re.compile(r"vending\s+route\s+advisory\s+call", re.IGNORECASE),
    re.compile(r"vending\s+route\s+discovery", re.IGNORECASE),
    re.compile(r"vending\s+consultation\s+&\s+strategy\s+session", re.IGNORECASE),
]

def _is_hard_excluded(meeting: dict) -> bool:
    """Returns True if this meeting should be ignored entirely regardless of type."""
    user_id = meeting.get("user_id") or ""
    if user_id in EXCLUDED_OWNERS:
        return True
    title = (meeting.get("title") or "").strip()
    if RE_CANCELED.match(title):
        return True
    if RE_FOLLOWUP.search(title):
        return True
    if re.search(r"\banthony\b", title, re.IGNORECASE) and re.search(r"\bq&a\b", title, re.IGNORECASE):
        return True
    if RE_ENROLLMENT.search(title):
        return True
    return False

def classify_meeting(meeting: dict) -> tuple:
    """
    Returns (tier, setter_name) where tier is one of:
      "closer"              — qualifying first sales/closer meeting
      "setter"              — discovery/setter meeting (not a closer call)
      "post_webinar"        — Post Masterclass Strategy Call (closer + post-webinar flag)
      "scraper"             — scraper meeting title (closer + scraper + setter name)
      "vendhub_consultation"— Vendhub Consultation Call (closer + VendHub = Standard Booking)
      "vendhub_nextsteps"   — Vendhub Next Steps Call (closer + VendHub = VendHub Q&A Booking)
      "react_email"         — Vending Consultation & Strategy Session (closer + Funnel Name = Reactivation Email)
      None                  — irrelevant, ignore

    setter_name is only populated for "scraper" tier.

    NOTE: Scraper titles are checked BEFORE hard excludes — they all contain
    "Next Steps" which would otherwise be caught by the followup hard exclude.
    SCRAPER_TITLE_MAP order matters: most specific (longest) pattern first.
    """
    user_id = meeting.get("user_id") or ""
    title   = (meeting.get("title") or "").strip()

    # Scraper check first — before hard excludes (titles contain "Next Steps")
    for pattern, setter_name in SCRAPER_TITLE_MAP:
        if pattern.search(title):
            if user_id not in EXCLUDED_OWNERS:
                return "scraper", setter_name

    # VendHub titles — checked before hard excludes ("Next Steps Call" hits followup filter)
    if RE_VENDHUB_NEXTSTEPS.search(title):
        if user_id not in EXCLUDED_OWNERS:
            return "vendhub_nextsteps", None
    if RE_VENDHUB_CONSULTATION.search(title):
        if user_id not in EXCLUDED_OWNERS:
            return "vendhub_consultation", None

    if _is_hard_excluded(meeting):
        return None, None

    # Setter by owner — Kristin or Spencer's meetings are always setter
    if user_id in SETTER_OWNERS:
        return "setter", None

    # Setter by title — Vending Quick Discovery (any owner)
    if RE_DISCOVERY_TITLE.search(title):
        return "setter", None

    # Post Masterclass Strategy Call — closer AND post-webinar flag
    if RE_POSTWEBINAR_TITLE.search(title):
        return "post_webinar", None

    # Route Planning Call — setter call that sets Funnel Name DEAL = VSL
    if RE_ROUTE_PLANNING_TITLE.search(title):
        return "route_planning", None

    # Vending Consultation & Strategy Session — closer + Funnel Name = Reactivation Email
    if RE_REACT_EMAIL_TITLE.search(title):
        return "react_email", None

    # Vending Consult Call — closer + sets Quick Discovery field
    if RE_QUICK_DISC_TITLE.search(title):
        return "quick_discovery", None

    # Closer — must match a qualifying pattern
    for pattern in CLOSER_PATTERNS:
        if pattern.search(title):
            return "closer", None

    return None, None


    # Setter by owner — Kristin or Spencer's meetings are always setter
    if user_id in SETTER_OWNERS:
        return "setter"

    # Setter by title — Vending Quick Discovery (any owner)
    if RE_DISCOVERY_TITLE.search(title):
        return "setter"

    # Post Masterclass Strategy Call — closer AND sets post-webinar flag
    if RE_POSTWEBINAR_TITLE.search(title):
        return "post_webinar"

    # Closer — must match a qualifying pattern
    for pattern in CLOSER_PATTERNS:
        if pattern.search(title):
            return "closer"

    return None

def latest_qualifying_dates_in_period(meetings, start: str, end: str) -> dict[str, str]:
    """Return each lead's latest active qualifying Pacific meeting date."""
    latest = {}
    seen_ids = set()
    for meeting in meetings:
        meeting_id = meeting.get("id")
        if meeting_id and meeting_id in seen_ids:
            continue
        if meeting_id:
            seen_ids.add(meeting_id)
        lead_id, starts_at = meeting.get("lead_id"), meeting.get("starts_at")
        if not lead_id or not starts_at:
            continue
        meeting_date = datetime.fromisoformat(
            starts_at.replace("Z", "+00:00")
        ).astimezone(PACIFIC).date().isoformat()
        if not start <= meeting_date <= end:
            continue
        if str(meeting.get("status") or "").strip().lower().startswith(CANCELED_PREFIXES):
            continue
        tier, _ = classify_meeting(meeting)
        if tier in QUALIFYING_TIERS and meeting_date > latest.get(str(lead_id), ""):
            latest[str(lead_id)] = meeting_date
    return latest
