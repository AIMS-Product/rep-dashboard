#!/usr/bin/env python3
"""Build Slack-formatted daily closer adherence messages without posting them."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from build_yesterday_preview import ROOT, build, load_env_value


PACIFIC = ZoneInfo("America/Los_Angeles")
STEP_LABELS = {
    "loom_usage": "Pre-Call Loom",
    "precall_text": "Pre-call text",
    "day_of_confirmation_text": "Day-of confirmation",
    "task_created": "Task created",
    "fu_meeting_created": "FU meeting created",
    "recap_email": "Recap email sent",
}
PHASE_STEPS = {
    "Pre-call": ("loom_usage", "precall_text", "day_of_confirmation_text"),
    "Post-call": ("task_created", "fu_meeting_created", "recap_email"),
}


def render_phase(lead: dict, phase: str) -> str:
    steps = lead.get("steps") or {}
    keys = PHASE_STEPS[phase]
    eligible = [key for key in keys if steps.get(key) in {"Completed", "Missed"}]
    completed = sum(steps.get(key) == "Completed" for key in eligible)
    parts = [
        f"{'✅' if steps[key] == 'Completed' else '❌'} {STEP_LABELS[key]}"
        for key in eligible
    ]
    exempt = [key for key in keys if steps.get(key) == "Exempt"]
    if exempt:
        parts.extend(f"➖ {STEP_LABELS[key]} (exempt)" for key in exempt)
    if not parts:
        parts = ["— no steps due yet"]
    return f"{phase} ({completed}/{len(eligible)} eligible): " + " · ".join(parts)


def render_messages(dashboard: dict, day: str) -> list[tuple[str, str]]:
    messages = []
    label_date = datetime.fromisoformat(day).strftime("%b %-d, %Y")
    for rep in dashboard.get("reps", []):
        if rep.get("exclude_meetings") or rep.get("is_manager"):
            continue
        adherence = rep.get("adherence") or {}
        leads = adherence.get("lead_results") or []
        lines = [f"*{rep['name']} — EOD | {label_date}*"]
        if not leads:
            lines.append("No leads booked for this day.")
        for index, lead in enumerate(leads, start=1):
            name = lead.get("name") or f"Lead {index}"
            url = lead.get("url") or ""
            heading = f"<{url}|{name}>" if url else name
            status = lead.get("opportunity_status_label") or "No opportunity"
            lines.extend((
                "",
                f"{index}. *{heading}* · Opp: `{status}`",
                render_phase(lead, "Pre-call"),
                render_phase(lead, "Post-call"),
            ))
        messages.append((rep["name"], "\n".join(lines)))
    return messages


def slack_blocks(message: str, *, separator_after: bool) -> list[dict]:
    lines = message.splitlines()
    heading = lines[0].strip("*") if lines else "Closer EOD"
    lead_sections: list[list[str]] = []
    current = []
    body_lines = lines[2:] if len(lines) > 1 and not lines[1].strip() else lines[1:]
    for line in body_lines:
        if not line.strip():
            if current:
                lead_sections.append(current)
                current = []
        else:
            current.append(line)
    if current:
        lead_sections.append(current)

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": heading}}]
    for section in lead_sections:
        if len(section) < 3 or not section[1].startswith("Pre-call (") or not section[2].startswith("Post-call ("):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(section)}})
            continue

        fields = []
        for phase_line in section[1:3]:
            phase, details = phase_line.split(": ", 1)
            rows = details.split(" · ")
            field_text = f"*{phase}*\n" + "\n".join(f"• {row}" for row in rows)
            fields.append({"type": "mrkdwn", "text": field_text})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": section[0]},
            "fields": fields,
        })

    if separator_after:
        blocks.append({"type": "divider"})
    return blocks


def send_messages(messages: list[tuple[str, str]], webhook_url: str) -> None:
    parsed = urlparse(webhook_url)
    if parsed.scheme != "https" or parsed.hostname != "hooks.slack.com" or not parsed.path.startswith("/services/"):
        raise ValueError("CLOSER_EOD_SLACK_WEBHOOK_URL must be an HTTPS Slack incoming webhook URL")

    fallback_text = "\n\n---\n\n".join(message for _, message in messages)
    blocks = [
        block
        for index, (_, message) in enumerate(messages)
        for block in slack_blocks(message, separator_after=index < len(messages) - 1)
    ]
    if len(fallback_text) > 40_000:
        raise ValueError("Combined closer EOD exceeds Slack's 40,000-character message limit")
    if len(blocks) > 50:
        raise ValueError(f"Combined closer EOD needs {len(blocks)} blocks; Slack allows at most 50 per message")

    request = Request(
        webhook_url,
        data=json.dumps({"text": fallback_text, "blocks": blocks}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(1024).decode("utf-8", errors="replace").strip()
            if response.status != 200 or body != "ok":
                raise RuntimeError(f"Slack rejected the combined EOD message (HTTP {response.status})")
    except HTTPError as error:
        raise RuntimeError(f"Slack rejected the combined EOD message (HTTP {error.code})") from None
    except URLError:
        raise RuntimeError("Could not reach Slack while sending the combined EOD message") from None
    print(f"Sent one EOD message covering {len(messages)} closers", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=datetime.now(PACIFIC).date().isoformat(),
        help="Pacific cohort date as YYYY-MM-DD (defaults to today)",
    )
    parser.add_argument(
        "--extract",
        type=Path,
        help="Reuse a previously captured private Close extract for local review",
    )
    parser.add_argument("--send", action="store_true", help="Post each closer's EOD message to Slack")
    parser.add_argument("--throttle", type=float, default=0.18)
    args = parser.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
        extract = args.extract or ROOT / ".private-audit" / f"close-extract-eod-{args.date}.json"
        dashboard = build(
            ROOT / ".private-audit" / f"eod-{args.date}.json",
            extract,
            throttle=args.throttle,
            reuse_extract=bool(args.extract),
            report_date=args.date,
            archive=False,
            write_local_preview=False,
        )
        day = dashboard["daily_meta"]["date"]
        messages = render_messages(dashboard, day)
        if not messages:
            raise RuntimeError("No closer rows were found in the dashboard data")
        if args.send:
            webhook_url = load_env_value(
                [ROOT.parent / ".env", ROOT / ".env"],
                "CLOSER_EOD_SLACK_WEBHOOK_URL",
            )
            if not webhook_url:
                raise RuntimeError("CLOSER_EOD_SLACK_WEBHOOK_URL was not found in the environment or workspace .env")
            send_messages(messages, webhook_url)
        rendered = "\n\n---\n\n".join(message for _, message in messages)
        if not args.send:
            print(rendered)
        summary_path = Path(os.environ["GITHUB_STEP_SUMMARY"]) if os.environ.get("GITHUB_STEP_SUMMARY") else None
        if summary_path:
            summary_path.write_text(rendered + "\n")
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
