"""Pure rules for the next rep scorecard; no production fetcher changes."""
from datetime import timedelta, timezone
from math import isfinite

from adherence_rules import (CANCELED_MEETING_STATUSES, _qualifying_tasks, is_active_activity,
                             is_outbound, message_time, parse_datetime, round_percent, is_post_call_exempt_status)

POST_CALL_HOURS = 12
from scheduled_sms_evidence import ALLOWED_PLAN_STATES

CASH_FIELD = 'cf_JP6Zdnv1ClODfctK5iwY9XseSeXEE9ZXbyopPw45OqZ'


QUALIFIED_FIELD = 'cf_ZDx7NBQaDzV1yYrFcBMzt6cIYj81dAcswpNN0CQzCPS'
SHOW_FIELD = 'cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq'

def post_call_exemption_reason(lead, meeting):
    if is_post_call_exempt_status(lead.get('status_id'), lead.get('status_label')):
        return 'Excluded lead status'
    opportunities = lead.get('opportunities') or []
    latest = max(opportunities, default={}, key=lambda row: (
        str(row.get('date_updated') or ''), str(row.get('date_created') or ''), str(row.get('id') or ''),
    ))
    status = ' '.join(str(latest.get('status_label') or '').lower().replace('/', ' ').replace('-', ' ').split())
    if status in {'lost', 'closed lost'}:
        return 'Closed Lost opportunity'
    custom = lead.get('custom') or {}
    qualified = lead.get('custom.' + QUALIFIED_FIELD, custom.get(QUALIFIED_FIELD, custom.get('Qualified (Opp)')))
    if qualified is False or str(qualified or '').strip().lower() == 'no':
        return 'Qualified = No'
    outcome = str(meeting.get('outcome') or '').lower().replace('-', ' ').strip()
    if is_post_call_exempt_status(None, outcome, meeting_outcome_id=meeting.get('outcome_id')):
        return 'Excluded call outcome'
    # Native meeting outcomes take precedence over the lead's fallback show flag.
    show = lead.get('custom.' + SHOW_FIELD, custom.get(SHOW_FIELD, custom.get('First Call Show Up (Opp)')))
    if not (meeting.get('outcome_id') or outcome) and str(show or '').strip().lower() == 'no':
        return 'No-show call'
    return None


def post_call_result(*, call_end, owner_id, now, call_start=None, emails=(), sms=(),
                     tasks=(), meetings=(), exempt=False):
    """Evaluate recap and next-step completion against the same 12-hour window.

    Actions completed during the call count. Any sent outbound email qualifies,
    matching the user's recap definition; a sent outbound SMS also counts.
    Email sender identity does not impose an additional restriction.

    Pending calls do not enter the denominator until their whole window has
    elapsed, even when the rep finishes early. This avoids counting only early
    successes while calls with time remaining are omitted.
    """
    if exempt:
        return {'state': 'exempt', 'recap_done': False, 'next_step_done': False}
    anchor = parse_datetime(call_end)
    if not anchor:
        return {'state': 'unknown', 'reason': 'Meeting end time unavailable',
                'recap_done': False, 'next_step_done': False}
    window_start = parse_datetime(call_start) or anchor
    if window_start > anchor:
        return {'state': 'unknown', 'reason': 'Meeting ends before it starts',
                'recap_done': False, 'next_step_done': False}
    deadline = (anchor.astimezone(timezone.utc) + timedelta(hours=POST_CALL_HOURS)).astimezone(anchor.tzinfo)
    last_observed = min(deadline, now)

    def in_window(value):
        stamp = parse_datetime(value)
        return bool(stamp and window_start.astimezone(timezone.utc) <= stamp.astimezone(timezone.utc)
                    <= last_observed.astimezone(timezone.utc))

    recaps = [m for m in (*emails, *sms)
              if is_outbound(m) and is_active_activity(m)
              and str(m.get('status') or '').lower() in {'sent', 'delivered', 'completed'}
              and in_window(message_time(m))]
    # Retain task eligibility and require creation within the same response window.
    next_tasks = [task for task in _qualifying_tasks(tasks, owner_id, window_start)
                  if in_window(task.get('date_created'))]
    next_meetings = [m for m in meetings
                     if str(m.get('status') or '').lower() not in CANCELED_MEETING_STATUSES
                     and (starts := parse_datetime(m.get('starts_at') or m.get('activity_at')))
                     and starts > window_start and in_window(m.get('date_created'))]
    recap_done = bool(recaps)
    next_sms, uncertain_sms = [], []
    for message in sms:
        if (not is_outbound(message) or not is_active_activity(message)
                or str(message.get('status') or '').lower() not in ALLOWED_PLAN_STATES
                or not message.get('date_scheduled')):
            continue
        created = parse_datetime(message.get('date_created'))
        send_at = parse_datetime(message.get('date_scheduled'))
        if (created and created > last_observed) or not send_at or send_at <= window_start:
            continue
        verified = bool(message.get('scheduling_history_complete', False)) and any(in_window(receipt.get('earliest_at')) and in_window(receipt.get('latest_at'))
                       and (target := parse_datetime(receipt.get('send_at'))) and target > window_start
                       for receipt in message.get('scheduling_evidence', []))
        if verified:
            next_sms.append(message)
        elif (not message.get('scheduling_history_complete', False) or any(
                (lo := parse_datetime(receipt.get('earliest_at'))) and
                (hi := parse_datetime(receipt.get('latest_at'))) and
                lo <= last_observed and hi >= window_start
                for receipt in message.get('scheduling_evidence', []))):
            uncertain_sms.append(message)
    next_step_done = bool(next_tasks or next_meetings or next_sms)
    return {
        'state': ('pending' if now < deadline else
                  'unknown' if uncertain_sms and not next_step_done else
                  'completed' if recap_done and next_step_done else 'missed'),
        'recap_done': recap_done,
        'next_step_done': next_step_done,
        'window_start_at': window_start.isoformat(),
        'call_end_at': anchor.isoformat(), 'deadline_at': deadline.isoformat(),
        'recap_ids': [m['id'] for m in recaps if m.get('id')],
        'task_ids': [t['id'] for t in next_tasks if t.get('id')],
        'meeting_ids': [m['id'] for m in next_meetings if m.get('id')],
        'scheduled_sms_ids': [m['id'] for m in next_sms if m.get('id')],
        'unverified_scheduled_sms_ids': [m['id'] for m in uncertain_sms if m.get('id')],
        **({'reason': 'SMS scheduling time could not be verified'} if uncertain_sms and not next_step_done else {}),
    }


def post_call_average(recap_done, next_step_done, eligible):
    # Both categories use the same cohort, so average before rounding.
    return round_percent(recap_done + next_step_done, 2 * eligible)


def aggregate_post_call(rows):
    due = [r for r in rows if r['state'] in {'completed', 'missed'}]
    done = sum(r['state'] == 'completed' for r in due)
    eligible = len(due)
    recap_done = sum(r['recap_done'] for r in due)
    next_step_done = sum(r['next_step_done'] for r in due)
    return {'done': done, 'eligible': eligible, 'pct': post_call_average(recap_done, next_step_done, eligible),
            'both_pct': round_percent(done, eligible),
            'recap_done': recap_done, 'next_step_done': next_step_done,
            **{state: sum(r['state'] == state for r in rows)
               for state in ('pending', 'exempt', 'unknown')}, 'leads': rows}


def owner_touch_result(lead, activities, as_of, hours):
    """Current-book follow-up snapshot; assignment dates never reset the clock."""
    touches = []
    other_recent = False
    cutoff = as_of.astimezone(timezone.utc) - timedelta(hours=hours)
    for activity in activities:
        if not is_outbound(activity):
            continue
        kind = str(activity.get('_type') or '').lower()
        status = str(activity.get('status') or '').lower()
        if kind == 'call' and status in {'completed', 'no-answer', 'busy', 'voicemail'}:
            stamp = parse_datetime(activity.get('date_started') or activity.get('activity_at') or activity.get('date_created'))
        elif kind in {'email', 'sms'} and status in {'sent', 'delivered'}:
            stamp = parse_datetime(activity.get('date_sent')) or message_time(activity)
        else:
            continue
        if not stamp or stamp > as_of:
            continue
        if activity.get('user_id') == lead['owner_id']:
            touches.append(stamp)
        elif stamp.astimezone(timezone.utc) > cutoff:
            other_recent = True
    last = max(touches, default=None)
    overdue = not last or last.astimezone(timezone.utc) <= cutoff
    return {'state': 'overdue' if overdue else 'within_window',
            'last_owner_touch_at': last.isoformat() if last else None,
            'hours_since_owner_touch': round((as_of.astimezone(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() / 3600, 1) if last else None,
            'other_sender_recent': other_recent}


def cash_collected(opportunity):
    """The numeric custom field contains dollars, unlike opportunity value cents.

    Missing is distinct from an explicit zero. Never substitute deal value.
    """
    custom = opportunity.get('custom') or {}
    raw = opportunity.get(f'custom.{CASH_FIELD}',
                          custom.get(CASH_FIELD, custom.get('💰 Cash Collected')))
    if raw is None or raw == '' or isinstance(raw, bool):
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    return amount if isfinite(amount) else None
