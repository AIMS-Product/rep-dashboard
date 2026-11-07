"""Pinned v2.8 eligibility with scheduled native outcomes; GET-only source access.

The pure calculation functions are carried from the reconciled local candidate.
Created bookings, period wins/revenue, and process scoring remain independent.
"""
from __future__ import annotations

from collections import defaultdict, Counter
from concurrent.futures import Future
from datetime import datetime, date, time, timedelta
from threading import Lock
from zoneinfo import ZoneInfo
import copy
import json

if __package__:
    from . import build as mtd
    from . import aggregates
else:
    import build as mtd
    import aggregates

PACIFIC = ZoneInfo('America/Los_Angeles')
METRIC_VERSION = 'calls-occurred.v1'


class ReportingReader:
    """One memoized GET-only input image shared by creation and scheduled replays."""
    def __init__(self, get, start, cutoff):
        if cutoff.tzinfo is None:
            raise ValueError('Reporting cutoff requires a timezone')
        self._get = get
        self.start = start.isoformat() if isinstance(start, date) else start
        self.cutoff = cutoff.astimezone(PACIFIC)
        self._requests = {}
        self._request_lock = Lock()
        self._catalog_lock = Lock()
        self._meetings = None
        self._users = None

    def get(self, endpoint, params=None):
        endpoint = endpoint.strip('/') + '/'
        key = (endpoint, json.dumps(params or {}, sort_keys=True))
        with self._request_lock:
            future = self._requests.get(key)
            owns_request = future is None
            if owns_request:
                future = Future()
                self._requests[key] = future
        if owns_request:
            try:
                future.set_result(self._get(endpoint, params))
            except Exception as exc:
                future.set_exception(exc)
                raise
        return copy.deepcopy(future.result())

    def pages(self, endpoint, params=None):
        return mtd.CloseReader.pages(self, endpoint.strip('/') + '/', params)

    @property
    def meetings(self):
        with self._catalog_lock:
            if self._meetings is None:
                self._meetings = mtd.CloseReader.meeting_catalog(self)
        return self._meetings

    @property
    def user_map(self):
        if self._users is None:
            self._users = {row['id']: ' '.join(filter(None, [row.get('first_name'), row.get('last_name')])) for row in self.pages('user/')}
        return self._users

    def meetings_created_in_window(self, start, end):
        lower, upper = mtd.parse_time(start), mtd.parse_time(end)
        return [copy.deepcopy(row) for row in self.meetings if (created := mtd.parse_time(row.get('date_created'))) and lower <= created <= upper]

    def meeting_history_for_leads(self, lead_ids):
        return [copy.deepcopy(row) for row in self.meetings if row.get('lead_id') in lead_ids]

def annotate_qualification(rows):
    """Assign distinct-lead accounting to a stable row; eligibility stays unchanged."""
    first={}
    for row in sorted(rows,key=lambda r:r['meeting_id']):
        key=(row['channel'],row['lead_id'])
        row['qualified_contribution']=int(bool(row['qualified']) and key not in first)
        if row['qualified']:first[key]=row['meeting_id']
        row['qualified_contribution_identity']=first.get(key,'') if row['qualified'] else ''

class HistoryReader:
    """Replay the authority over complete histories for scheduled candidate leads."""
    def __init__(self,source,subjects):self.source=source;self.subjects=subjects
    def pages(self,endpoint,params=None):
        if endpoint.strip('/') in {'lead','opportunity'}:return []
        return self.source.pages(endpoint,params)
    def get(self,endpoint,params=None):return self.source.get(endpoint,params)
    def meetings_created_in_window(self,*args):
        return [r for r in self.source.meetings if r.get('lead_id') in self.subjects]
    def meeting_history_for_leads(self,lids):return self.source.meeting_history_for_leads(lids)

def scheduled_dataset(reader):
    lower=datetime.combine(date.fromisoformat(reader.start),time.min,PACIFIC)
    subjects={r['lead_id'] for r in reader.meetings if r.get('lead_id') and (starts:=mtd.parse_time(r.get('starts_at'))) and lower<=starts<reader.cutoff and mtd.preliminary_bucket(r)}
    times=[mtd.parse_time(r.get('date_created')) for r in reader.meetings if r.get('lead_id') in subjects]
    times=[t for t in times if t]
    oldest=min(times).astimezone(PACIFIC).date() if times else date.fromisoformat(reader.start)
    history=mtd.build_dataset(HistoryReader(reader,subjects),reader.cutoff,oldest)
    rows=[r for r in history['rows'] if lower<=mtd.parse_time(r['starts_at'])<reader.cutoff]
    for row in rows:row['owner']=aggregates.owner_name(row['owner'],reader.user_map)
    annotate_qualification(rows)
    totals=rollup(rows)
    return {'source_cutoff':reader.cutoff.isoformat(),'period_start':reader.start,'placement':'scheduled starts_at in [Pacific period start, cutoff), independent of date_created','rows':rows,'totals':totals,'history_authority_rules':history['rules_version'],'scope':history['booking_scope']}

def rollup(rows):
    return {'scheduled_calls':len(rows),'showed':sum(r['showed'] for r in rows),'qualified':len({(r['channel'],r['lead_id']) for r in rows if r['qualified']})}

def by_owner(rows):
    groups=defaultdict(list)
    for row in rows:groups[row['owner']].append(row)
    return groups

def occurred_kind(row):
    if str(row.get('status', '')).lower() in {'canceled', 'cancelled', 'declined'}:
        return None
    return {'completed': 'show', 'no show': 'no_show'}.get(str(row.get('outcome', '')).strip().lower())

def rate(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else None

def occurred_dataset(scheduled):
    rows = copy.deepcopy([r for r in scheduled['rows'] if occurred_kind(r)])
    annotate_qualification(rows)
    for row in rows:
        row['occurred_kind'] = occurred_kind(row)
    return {
        **{k: v for k, v in scheduled.items() if k not in {'rows', 'totals', 'placement'}},
        'placement': 'Accepted v2.8 calls scheduled in [Pacific period start, cutoff), with native Completed or No Show outcome; independent of creation date',
        'rows': rows,
        'totals': {
            'occurred_calls': len(rows),
            'showed': sum(occurred_kind(r) == 'show' for r in rows),
            'no_shows': sum(occurred_kind(r) == 'no_show' for r in rows),
            'qualified': len({(r['channel'], r['lead_id']) for r in rows if r['qualified']}),
            'scheduled_calls': len(scheduled['rows']),
            'unresolved_calls': len(scheduled['rows']) - len(rows),
        },
    }

def record(row):
    return {'lead_id': row['lead_id'], 'meeting_id': row['meeting_id'],
            'name': row['lead_name'], 'date': row['scheduled_date_pt'],
            'meeting_outcome': row['outcome'], 'meeting_status': row['status'],
            'funnel': row['channel']}

def apply_occurred_payload(previous, occurred, scheduled):
    data = copy.deepcopy(previous)
    groups = by_owner(occurred['rows'])
    elapsed_groups = by_owner(scheduled['rows'])
    for rep in data['reps']:
        rows = groups.get(rep['name'], [])
        elapsed = elapsed_groups.get(rep['name'], [])
        rep.update(occurred_calls=len(rows), shown=sum(occurred_kind(r) == 'show' for r in rows),
                   no_shows=sum(occurred_kind(r) == 'no_show' for r in rows),
                   qualified=len({r['lead_id'] for r in rows if r['qualified']}),
                   unresolved_calls=len(elapsed) - len(rows), scheduled_calls=len(elapsed))
        rep.update(show_rate=rate(rep['shown'], len(rows)),
                   qualification_rate=rate(rep['qualified'], len(rows)),
                   close_rate=rate(rep['deals'], len(rows)),
                   revenue_per_occurred=round(rep['revenue'] / len(rows), 2) if rows else None)
        # Preserve original Booked/deal drawers; outcome drawers use the occurred cohort.
        details = rep.setdefault('metric_details', {})
        details['occurred_calls'] = [record(r) for r in rows]
        details['shown'] = [record(r) for r in rows if occurred_kind(r) == 'show']
        details['no_shows'] = [record(r) for r in rows if occurred_kind(r) == 'no_show']
        details['no_outcome'] = [record(r) for r in elapsed if not occurred_kind(r)]
        details['upcoming'] = []
        seen = set()
        details['qualified'] = []
        for row in rows:
            if row['qualified'] and row['lead_id'] not in seen:
                details['qualified'].append(record(row))
                seen.add(row['lead_id'])
    totals = occurred['totals']
    data.update(total_occurred_calls=totals['occurred_calls'], total_no_shows=totals['no_shows'],
                total_unresolved_calls=totals['unresolved_calls'], total_shown=totals['showed'],
                total_qualified=totals['qualified'])
    data.update(team_show_rate=rate(data['total_shown'], data['total_occurred_calls']),
                team_qualification_rate=rate(data['total_qualified'], data['total_occurred_calls']),
                team_close_rate=rate(data['total_deals'], data['total_occurred_calls']))
    data['alignment_metadata'].update(occurred_basis=occurred['placement'],
        shown_qualified_basis=occurred['placement'],
        closed_won_basis='Native date_won in reporting period, independent of scheduled or booked cohort',
        rate_basis='Show, qualification and close rates divide by Calls Occurred; undefined at zero calls',
        process_basis='Native scheduled-date process cohort and scoring remain unchanged')
    for key in ('booked', 'deals', 'revenue', 'adherence'):
        assert {r['name']: r.get(key) for r in data['reps']} == {r['name']: r.get(key) for r in previous['reps']}, key
    assert sum(r['occurred_calls'] for r in data['reps']) == data['total_occurred_calls']
    assert sum(r['shown'] for r in data['reps']) == data['total_shown']
    assert sum(r['qualified'] for r in data['reps']) == data['total_qualified']
    return data

def use_occurred_as_booked(payload, occurred, source_map=None):
    """Sales surfaces call the occurred cohort Booked; MTD keeps both metrics."""
    data = copy.deepcopy(payload)
    data['total_created_booked'] = data['total_booked']
    data['total_booked'] = data['total_occurred_calls']
    for rep in data['reps']:
        rep['created_booked'] = rep['booked']
        rep['booked'] = rep['occurred_calls']
        rep['revenue_per_lead'] = rep['revenue_per_occurred']
        rows = [r for r in occurred['rows'] if r['owner'] == rep['name']]
        for key, bucket in [('marketing', 'Marketing'), ('lane2', 'Lane 2'), ('social_dms', 'Social DMs')]:
            rep['created_' + key] = rep.get(key, 0)
            rep[key] = sum(r['bucket'] == bucket for r in rows)
        rep['metric_details']['created_booked'] = rep['metric_details'].get('booked', [])
        rep['metric_details']['booked'] = copy.deepcopy(rep['metric_details']['occurred_calls'])
    if 'funnel_breakdown' in data:
        channels = Counter(r['channel'] for r in occurred['rows'])
        data['funnel_breakdown'] = [{'funnel': name, 'count': count} for name, count in channels.items()]
        counts = Counter()
        for channel, count in channels.items():
            source = (source_map or {}).get(channel)
            counts['in_house' if source == 'In-House' else 'external' if source == 'External' else 'unknown'] += count
        data['funnel_sources'] = {**counts, **{key + '_pct': rate(counts[key], data['total_booked']) for key in ('in_house', 'external', 'unknown')}}
    data['alignment_metadata']['booked_basis'] = 'Calls Occurred: accepted scheduled calls with native Completed or No Show outcome; creation-date bookings are shown only on MTD V2'
    assert sum(rep['booked'] for rep in data['reps']) == data['total_booked']
    return data


def apply_dashboard_outcomes(payload, reader, *, surface, source_map=None):
    """Replace call metrics only; retain native date-won and process measures."""
    scheduled = scheduled_dataset(reader)
    occurred = occurred_dataset(scheduled)
    base = copy.deepcopy(payload)
    base.setdefault('alignment_metadata', {})
    base['total_scheduled_calls'] = len(scheduled['rows'])
    result = use_occurred_as_booked(apply_occurred_payload(base, occurred, scheduled), occurred, source_map)
    result['metric_definitions'] = {
        'version': METRIC_VERSION,
        'booking_rules': mtd.RULES['version'],
        'booked': 'native_completed_plus_no_show',
        'period_start': reader.start,
        'cutoff_exclusive': reader.cutoff.isoformat(),
        'timezone': 'America/Los_Angeles',
        'qualification': 'distinct_current_qualified_leads_in_occurred_cohort',
        'closed_won': 'native_date_won_independent_of_meeting_dates',
        'rates': 'shown_qualified_and_period_wins_divided_by_occurred_calls',
    }
    # Keep the existing public boundary. Source snapshots, legacy comparison rows
    # and private activity identities are never published by this adapter.
    result.pop('alignment_metadata', None)
    result.pop('total_created_booked', None)
    for rep in result['reps']:
        for key in list(rep):
            if key.startswith('created_'):
                rep.pop(key)
        if surface == 'rep':
            rep.pop('metric_details', None)
        else:
            details = rep['metric_details']
            details.pop('created_booked', None)
            details.pop('occurred_calls', None)
            for records in details.values():
                if isinstance(records, list):
                    for entry in records:
                        entry.pop('meeting_id', None)
    return result
