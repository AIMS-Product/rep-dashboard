"""Pure rules for the next rep scorecard; no production fetcher changes."""
from datetime import timedelta, timezone
from math import isfinite

from adherence_rules import (CANCELED_MEETING_STATUSES, _qualifying_tasks, is_active_activity,
                             is_outbound, message_time, parse_datetime, round_percent)

POST_CALL_HOURS = 12
CASH_FIELD = 'cf_JP6Zdnv1ClODfctK5iwY9XseSeXEE9ZXbyopPw45OqZ'


def post_call_result(*, call_end, owner_id, now, call_start=None, emails=(), sms=(),
                     tasks=(), meetings=(), exempt=False):
    """Require email within 12h AND a task or follow-up meeting created within 12h.

    Actions completed during the call count. Any sent outbound email qualifies,
    matching the user's recap definition; SMS is not email and does not count.
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

    recaps = [m for m in emails
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
    next_step_done = bool(next_tasks or next_meetings)
    return {
        'state': ('pending' if now < deadline else
                  'completed' if recap_done and next_step_done else 'missed'),
        'recap_done': recap_done,
        'next_step_done': next_step_done,
        'window_start_at': window_start.isoformat(),
        'call_end_at': anchor.isoformat(), 'deadline_at': deadline.isoformat(),
        'recap_ids': [m['id'] for m in recaps if m.get('id')],
        'task_ids': [t['id'] for t in next_tasks if t.get('id')],
        'meeting_ids': [m['id'] for m in next_meetings if m.get('id')],
    }


def aggregate_post_call(rows):
    due = [r for r in rows if r['state'] in {'completed', 'missed'}]
    done = sum(r['state'] == 'completed' for r in due)
    return {'done': done, 'eligible': len(due), 'pct': round_percent(done, len(due)),
            'recap_done': sum(r['recap_done'] for r in due),
            'next_step_done': sum(r['next_step_done'] for r in due),
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
