"""Local provider fixture installed only in the joint publisher test subprocesses."""

import io
import json
import os
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit

import requests

OWNER = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
OFFER = "cf_aJlNlilQZIgLLuhcymNN8fiOzewnFxrbWjLZFPmsucO"
WARM = "stat_mRxbAkfEqzEcmEF2Z5CkucMQocXAbwzs0hSlD0SzHEh"
PIPELINE = "pipe_78hyBUVS7IKikGEmstObu1"
USERS = [
    dict(id="user_scott", first_name="Scott", last_name="Seymour", email="scott@example.test"),
    dict(id="user_luke", first_name="Luke", last_name="Herman", email="luke@example.test"),
    dict(id="user_joe", first_name="Joe", last_name="Dysert", email="joe@example.test"),
]


def lead(identifier, owner, status=WARM, label="Warm Follow Up", **fields):
    return {"id": "lead_" + identifier, "display_name": identifier.title(),
            "status_id": status, "status_label": label,
            f"custom.{OWNER}": owner, f"custom.{OFFER}": "Vendingpreneurs (VP)",
            "custom": {OWNER: owner, OFFER: "Vendingpreneurs (VP)"},
            "date_created": "2026-10-26T16:00:00Z", **fields}


LEADS = {row["id"]: row for row in [
    lead("transferred", "user_luke"),
    lead("lost", "user_scott", status="example_open", label="Open",
         opportunities=[{"id": "opportunity_lost", "status_label": "Closed / Lost"}]),
    lead("joe", "user_joe", status="example_open", label="Open"),
    lead("current", "user_luke", status="example_open", label="Open"),
    lead("unresolved", "user_luke", status="example_open", label="Open"),
]}


def meeting(identifier, owner, starts, ends, *, outcome="outcome_completed"):
    return {"id": "meeting_" + identifier, "lead_id": "lead_" + identifier,
            "user_id": owner, "users": [owner, "user_luke"], "title": "Vending Strategy Call",
            "date_created": "2026-10-26T16:00:00Z", "starts_at": starts, "ends_at": ends,
            "status": "completed", "outcome_id": outcome}


MEETINGS = [
    meeting("transferred", "user_scott", "2026-10-31T17:00:00Z", "2026-10-31T18:00:00Z"),
    meeting("lost", "user_scott", "2026-10-31T19:00:00Z", "2026-10-31T20:00:00Z"),
    meeting("joe", "user_joe", "2026-11-02T06:30:00Z", "2026-11-02T07:00:00Z"),
    meeting("current", "user_luke", "2026-11-02T17:00:00Z", "2026-11-02T18:00:00Z"),
    meeting("unresolved", "user_luke", "2026-11-02T19:00:00Z", "2026-11-02T20:00:00Z", outcome=""),
]


def opportunity(identifier, won, amount):
    return dict(id="opportunity_" + identifier, lead_id="lead_joe", date_won=won,
                user_id="user_joe", value=amount, currency="USD", pipeline_id=PIPELINE)


WINS = [opportunity("october", "2026-10-30", 125000),
        opportunity("november", "2026-11-02", 75000)]


def message(identifier, lead_id, timestamp, *, owner="user_joe", text="Follow-up recap"):
    return dict(id=identifier, lead_id=lead_id, user_id=owner, date_sent=timestamp,
                activity_at=timestamp, status="sent", direction="outbound", body_text=text)


EMAILS = [
    message("loom", "lead_joe", "2026-11-01T19:00:00Z", text="https://www.loom.com/share/example"),
    message("mondayrecap", "lead_joe", "2026-11-02T09:00:00Z"),
]
SMS = [
    message("confirmation", "lead_joe", "2026-11-01T23:00:00Z"),
    message("ownertouch", "lead_transferred", "2026-11-03T16:00:00Z", owner="user_luke"),
]
TASKS = [dict(id="task_monday", lead_id="lead_joe", assigned_to="user_joe",
              date_created="2026-11-02T10:00:00Z", date="2026-11-05T20:00:00Z",
              due_date="2026-11-05T20:00:00Z", _type="lead")]


def evaluation(identifier, email, start, score):
    return {"scorecard_template_name": "VP Closing Call Scorecard",
            "scored_for": {"email": email}, "meeting": {"uuid": identifier, "start_at": start},
            "overall_score": score, "modified": "2026-11-03T15:00:00Z"}


QA = [
    evaluation("october", "scott@example.test", "2026-10-31T17:00:00Z", 2),
    evaluation("sunday", "joe@example.test", "2026-11-02T06:30:00Z", 5),
    evaluation("monday", "luke@example.test", "2026-11-02T17:00:00Z", 3),
]


def recorded(host, path, params, method):
    entry = json.dumps({"host": host, "path": path, "params": params, "method": method})
    with open(os.environ["WEEKLY_FIXTURE_TRAFFIC"], "a") as output:
        output.write(entry + "\n")


def route(url, method="GET", params=None):
    parsed = urlsplit(url)
    fields = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    fields.update(params or {})
    recorded(parsed.hostname, parsed.path, fields, method)
    if parsed.hostname == "api.avoma.com" and parsed.path == "/v1/scorecard_evaluations/":
        return {"results": QA, "next": None}
    if parsed.hostname != "api.close.com" or not parsed.path.startswith("/api/v1/"):
        raise AssertionError(f"Unexpected provider route {parsed.hostname} {parsed.path}")
    path = parsed.path.removeprefix("/api/v1/").strip("/")
    if path == "data/search" and method == "POST":
        return {"data": [LEADS["lead_transferred"]], "cursor": None}
    if path.startswith("lead/"):
        return LEADS[path.split("/")[1]]
    lead_ids = fields.get("lead_id", "").split(",")
    scope = {
        "user": USERS,
        "outcome": [{"id": "outcome_completed", "label": "Completed"}],
        "activity/meeting": MEETINGS,
        "activity/email": EMAILS,
        "activity/sms": SMS,
        "activity": [{**row, "_type": "SMS"} for row in SMS],
        "task": TASKS,
        "activity/note": [],
        "activity/call": [],
        "contact": [],
        "lead": [],
        "status/opportunity": [],
        "event": [],
    }
    if path == "opportunity":
        rows = [row for row in WINS if fields.get("date_won__gte", "9999") <= row["date_won"]
                <= fields.get("date_won__lte", "0000")]
    elif path in scope:
        rows = scope[path]
        if lead_ids != [""]:
            rows = [row for row in rows if row.get("lead_id") in lead_ids]
        if fields.get("assigned_to"):
            rows = [row for row in rows if row.get("assigned_to") == fields["assigned_to"]]
    else:
        raise AssertionError(f"Unexpected fixture path {path}")
    return {"data": rows, "has_more": False}


def requests_call(self, method, url, **kwargs):
    payload = route(url, method=method, params=kwargs.get("params"))
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(payload).encode()
    return response


def urllib_call(request, *args, **kwargs):
    return io.BytesIO(json.dumps(route(request.full_url, request.get_method())).encode())


if os.environ.get("WEEKLY_FIXTURE_TRAFFIC"):
    requests.sessions.Session.request = requests_call
    urllib.request.urlopen = urllib_call
    time.sleep = lambda seconds: None
