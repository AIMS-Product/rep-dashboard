"""Verify scheduled SMS plans from Close scheduling transitions, not draft dates."""
from datetime import datetime, timezone

ALLOWED_PLAN_STATES = {'scheduled', 'outbox', 'sent', 'delivered'}


def event_time(value):
    if not value:
        return None
    stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def scheduling_receipts(events):
    """Return conservative event-time bounds, including consolidated updates."""
    receipts = []
    for event in reversed(events):
        data = event.get('data') or {}
        previous = event.get('previous_data') or {}
        if event.get('action') == 'deleted' or data.get('status') in {'draft', 'error', 'failed', 'deleted', 'canceled'}:
            receipts.clear()
        target = event_time(data.get('date_scheduled'))
        changed = set(event.get('changed_fields') or [])
        created_scheduled = event.get('action') == 'created' and data.get('status') == 'scheduled'
        became_scheduled = (event.get('action') == 'updated'
            and data.get('status') in ALLOWED_PLAN_STATES
            and (('status' in changed and previous.get('status') == 'draft' and data.get('status') == 'scheduled')
                 or ('date_scheduled' in changed and previous.get('date_scheduled') != data.get('date_scheduled'))))
        if not target or not (created_scheduled or became_scheduled):
            continue
        stamps = [event_time(event.get('date_created')), event_time(event.get('date_updated'))]
        stamps = [stamp for stamp in stamps if stamp is not None]
        if not stamps:
            continue
        receipts.append({'event_id': event.get('id'), 'earliest_at': min(stamps).isoformat(),
                         'latest_at': max(stamps).isoformat(), 'send_at': target.isoformat()})
    return receipts


def collect_scheduling_receipts(client, messages, as_of):
    for message in messages:
        if not message.get('date_scheduled') or message.get('status') not in ALLOWED_PLAN_STATES:
            continue
        events, cursor, seen = [], None, set()
        complete = True
        while True:
            params = {'object_type': 'activity.sms', 'object_id': message['id'], '_limit': 50}
            if cursor:
                params['_cursor'] = cursor
            page = client.get('/event/', params)
            if not isinstance(page.get('data'), list) or 'cursor_next' not in page:
                raise ValueError('Incomplete SMS scheduling event response')
            for event in page['data']:
                if event.get('object_id') != message['id'] or event.get('object_type') != 'activity.sms':
                    raise ValueError('SMS scheduling event escaped object scope')
                if event.get('action') in {'created', 'updated'}:
                    data = event.get('data') or {}
                    if (not isinstance(data, dict) or data.get('id') != message['id']
                            or not isinstance(data.get('status'), str)
                            or not data.get('date_created') or not data.get('date_updated')):
                        complete = False
                    changed = event.get('changed_fields') or []
                    previous = event.get('previous_data') or {}
                    if 'status' in changed and not isinstance(previous.get('status'), str):
                        complete = False
                events.append(event)
            cursor = page['cursor_next']
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise ValueError('Invalid SMS event pagination cursor')
            seen.add(cursor)
        # Retain proof only for observed transitions, never manufacture a queue time.
        message['scheduling_evidence'] = scheduling_receipts(events)
        created = event_time(message.get('date_created'))
        message['scheduling_history_complete'] = bool(complete and any(e.get('action') == 'created' for e in events) and created
            and (as_of.astimezone(timezone.utc) - created).total_seconds() < 30 * 86400)
    return messages
