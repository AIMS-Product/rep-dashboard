"""Read metadata for a fixed dashboard cohort and build a local scorecard preview."""
import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from adherence_rules import PACIFIC, parse_datetime, round_percent
from build_adherence_preview import (CloseClient, load_env_value, add_adherence_to_dashboard,
                                      LATEST_BOOKED_DATE_FIELD)
from scorecard_rules import CASH_FIELD, aggregate_post_call, cash_collected, post_call_result, owner_touch_result
from rep_roster import INACTIVE_CLOSERS
from scorecard_details import attach_financial_details

PIPELINE_ID = 'pipe_78hyBUVS7IKikGEmstObu1'
WON_STATUS_ID = 'stat_WnFc0uhjcjV0cc3bVzdFVqDz7av6rbsOmOvHUsO6s03'


def apply_preview_roster(dashboard, *, process_extract=None):
    """Remove departed rows and their call/QA cohorts; preserve earned revenue."""
    retired = [rep for rep in dashboard['reps'] if rep['name'] in INACTIVE_CLOSERS]
    if not retired:
        return dashboard
    if process_extract is None:
        raise ValueError('A complete process extract is required to rebuild a changed roster')
    dashboard['reps'] = [rep for rep in dashboard['reps'] if rep['name'] not in INACTIVE_CLOSERS]
    for total, field in [('total_booked','booked'), ('total_shown','shown'),
                         ('total_qualified','qualified'), ('total_no_shows','no_shows'),
                         ('total_occurred_calls','occurred_calls'), ('total_scheduled_calls','scheduled_calls'),
                         ('total_unresolved_calls','unresolved_calls')]:
        dashboard[total] -= sum(rep.get(field, 0) for rep in retired)
    denominator = dashboard['total_occurred_calls']
    for target, numerator in [('team_show_rate', 'total_shown'),
                              ('team_qualification_rate', 'total_qualified'),
                              ('team_close_rate', 'total_deals')]:
        dashboard[target] = round(dashboard[numerator] / denominator * 100, 1) if denominator else None
    count = sum(rep.get('qa_score_count', 0) for rep in dashboard['reps'] if rep.get('qa_score') is not None)
    dashboard['team_qa_score_count'] = count
    dashboard['team_qa_score'] = sum(rep['qa_score'] * rep.get('qa_score_count', 0)
        for rep in dashboard['reps'] if rep.get('qa_score') is not None) / count if count else None
    dashboard = add_adherence_to_dashboard(
        dashboard, extract=process_extract, preview_only=True,
        month_override=dashboard['adherence_meta']['period']['month'],
        candidate_booked_date_field=LATEST_BOOKED_DATE_FIELD,
        include_canceled_by_lead_status=True,
    )
    dashboard['adherence_meta']['preview_roster_exclusions'] = [rep['name'] for rep in retired]
    return dashboard


def cohort_for_rep(rep):
    cohorts = rep.get('adherence', {}).get('lead_cohorts', {})
    result = {}
    for key in ('follow_up_created', 'recap_email'):
        for bucket, leads in cohorts.get(key, {}).items():
            for lead in leads:
                result.setdefault(lead['id'], dict(lead, exempt=False))
                if bucket == 'exempt':
                    result[lead['id']]['exempt'] = True
    return list(result.values())


def collect(client, dashboard, *, as_of=None, opportunities=None):
    started = as_of or datetime.now(PACIFIC)
    by_rep = {rep['rep_owner_id']: cohort_for_rep(rep) for rep in dashboard['reps']}
    ids = sorted({lead['id'] for leads in by_rep.values() for lead in leads if not lead['exempt']})
    activity = {}
    fields = {
        'emails': 'id,lead_id,user_id,status,direction,date_sent,date_created,activity_at',
        'sms': 'id,lead_id,user_id,status,direction,date_sent,date_created,activity_at',
        'meetings': 'id,lead_id,user_id,status,starts_at,ends_at,date_created,activity_at',
    }
    for kind, selected_fields in fields.items():
        grouped = defaultdict(list)
        endpoint = {'emails': 'email', 'sms': 'sms', 'meetings': 'meeting'}[kind]
        for offset in range(0, len(ids), 25):
            batch = ids[offset:offset + 25]
            for row in client.paginate(f'/activity/{endpoint}/',
                                      {'lead_id': ','.join(batch), '_fields': selected_fields}):
                if row.get('lead_id') not in batch:
                    raise ValueError('Activity read escaped requested lead scope')
                grouped[row['lead_id']].append(row)
        activity[kind] = dict(grouped)
        print(f'Collected {kind} metadata for post-call cohort', flush=True)
    tasks = {}
    for i, lead_id in enumerate(ids):
        tasks[lead_id] = list(client.paginate('/task/', {'lead_id': lead_id, 'view': 'all',
            '_fields': 'id,lead_id,_type,assigned_to,date,date_created,due_date,status'}))
        if (i + 1) % 25 == 0 or i + 1 == len(ids):
            print(f'Collected task metadata for {i + 1}/{len(ids)} leads', flush=True)
    month = dashboard['adherence_meta']['period']['month']
    opportunities = opportunities if opportunities is not None else list(client.paginate('/opportunity/', {
        'status_id': WON_STATUS_ID, 'date_won__gte': month + '-01',
        'date_won__lte': started.date().isoformat(),
        '_fields': f'id,lead_id,lead_name,user_id,pipeline_id,date_won,custom,custom.{CASH_FIELD}',
    }))
    ended = datetime.now(PACIFIC)
    return {'meta': {'started_at': started.isoformat(), 'ended_at': ended.isoformat(),
                     'as_of': started.isoformat(), 'api_requests': client.request_count},
            'cohorts': by_rep, 'activities': activity, 'tasks': tasks,
            'opportunities': [o for o in opportunities if o.get('pipeline_id') == PIPELINE_ID]}


def enrich(dashboard, capture):
    as_of = parse_datetime(capture['meta']['as_of'])
    activity = capture['activities']
    all_rows = []
    for rep in dashboard['reps']:
        rows = []
        for lead in capture['cohorts'].get(rep['rep_owner_id'], []):
            lead_id = lead['id']
            starts = parse_datetime(lead.get('scored_call_at'))
            matching = [m for m in activity['meetings'].get(lead_id, [])
                        if starts and parse_datetime(m.get('starts_at') or m.get('activity_at')) == starts]
            if len(matching) > 1:
                matching.sort(key=lambda m: (str(m.get('status') or '').lower() in
                    {'canceled', 'declined-by-lead', 'declined-by-org'}, m.get('id', '')))
            meeting = matching[0] if matching else {}
            end = parse_datetime(meeting.get('ends_at'))
            if end and starts and end < starts:
                end = None
            evidence = post_call_result(call_end=end, call_start=starts, owner_id=rep['rep_owner_id'], now=as_of,
                exempt=lead['exempt'], emails=activity['emails'].get(lead_id, []),
                sms=activity['sms'].get(lead_id, []), tasks=capture['tasks'].get(lead_id, []),
                meetings=activity['meetings'].get(lead_id, []))
            rows.append(dict(lead, **evidence))
        rep['post_call_12h'] = aggregate_post_call(rows)
        adherence = rep.get('adherence')
        if adherence is not None:
            adherence['post_call_12h'] = rep['post_call_12h']
            adherence['post_call_pct'] = rep['post_call_12h']['pct']
            for key, label, flag in [('post_call_complete_12h', 'Email ≤12h AND next step', None),
                                     ('recap_12h', 'Recap sent ≤12h', 'recap_done'),
                                     ('next_step_12h', 'Task or follow-up meeting created ≤12h', 'next_step_done')]:
                due = [r for r in rows if r['state'] in {'completed', 'missed'}]
                done = sum(r[flag] if flag else r['state'] == 'completed' for r in due)
                adherence['steps'][key] = {'label': label, 'phase': 'post_call', 'done': done,
                    'eligible': len(due), 'pct': round_percent(done, len(due)),
                    **{state: rep['post_call_12h'][state] for state in ('pending', 'exempt', 'unknown')}}
                cohorts = {bucket: [] for bucket in ('completed', 'missed', 'pending', 'exempt', 'unknown')}
                for row in rows:
                    bucket = row['state']
                    if bucket in {'completed', 'missed'} and flag:
                        bucket = 'completed' if row[flag] else 'missed'
                    cohorts[bucket].append(row)
                adherence['lead_cohorts'][key] = cohorts
        all_rows.extend(rows)
        rep_opps = [o for o in capture['opportunities'] if o.get('user_id') == rep['rep_owner_id']]
        amounts = [cash_collected(o) for o in rep_opps]
        rep['cash_collected'] = {
            'amount': round(sum(a for a in amounts if a is not None), 2) if any(a is not None for a in amounts) else None,
            'recorded': sum(a is not None for a in amounts), 'opportunities': len(amounts),
            'missing': sum(a is None for a in amounts),
        }
    dashboard['team_post_call_12h'] = aggregate_post_call(all_rows)
    cash = [r['cash_collected'] for r in dashboard['reps']]
    dashboard['team_cash_collected'] = {
        'amount': round(sum(c['amount'] or 0 for c in cash), 2) if any(c['recorded'] for c in cash) else None,
        **{key: sum(c[key] for c in cash) for key in ('recorded', 'opportunities', 'missing')},
    }
    dashboard['scorecard_meta'] = {
        **capture['meta'], 'preview_only': True, 'post_call_hours': 12,
        'post_call_scoring': 'category_average',
        'anchor': 'Actions count from meeting starts_at; deadline is 12 elapsed hours after ends_at (scheduled end)',
        'recap_basis': 'Any sent outbound email on the lead; no content/template/sender restriction; SMS excluded',
        'next_step_basis': 'Existing task-or-follow-up-meeting eligibility, plus creation within the same 12-hour response window',
        'cohort_basis': 'Existing post-call exemptions; full active-roster process cohort; any-sender pre-call Loom links; VP/blank business-line process scope',
        'cash_basis': 'Cash Collected field on Sales Pipeline opportunities won this month, displayed owners only; not receipt-date reporting',
        'close_basis': 'Reuse existing rep dashboard close_rate and team_close_rate: closed / (shown + no-show), with its existing first-call eligibility and repeat-call rules',
        'targets': {'pre_call': 90, 'show_rate': 75, 'post_call_12h': 95,
                    'hot_followup': 95, 'warm_followup': 90, 'close_rate': 18,
                    'cash_collected': 90000, 'qa': 80},
    }
    return attach_financial_details(dashboard, capture['opportunities'])


def add_followup_snapshot(dashboard, capture):
    """Use verified current owners/statuses; never relabel failed reads compliant."""
    definitions = [('hot_followup_72h', 72, {'stat_kY1aMGKOui3jjlgniY2LQWMadXN78cr7vTHVMPDCliy'}),
                   ('warm_followup_5d', 120, {'stat_fOwpEPCfmjC2LhrJEZE5T1btfKJRcgkMinycBxLPwS5',
                                            'stat_mRxbAkfEqzEcmEF2Z5CkucMQocXAbwzs0hSlD0SzHEh'})]
    as_of = parse_datetime(capture['meta']['started_at'])
    errors = [row for row in capture['results'] if row.get('error')]
    if errors:
        raise ValueError(f'Follow-up capture has {len(errors)} unverified leads')
    for key, hours, statuses in definitions:
        groups = []
        for rep in dashboard['reps']:
            rows = []
            for item in capture['results']:
                lead = item['lead']
                if (lead.get('owner_id') != rep['rep_owner_id'] or lead.get('status_id') not in statuses
                        or lead.get('business_line') not in {'', 'Vendingpreneurs (VP)'}):
                    continue
                rows.append(dict(lead, url=f"https://app.close.com/lead/{lead['id']}/",
                                 **owner_touch_result(lead, item['activities'], as_of, hours)))
            rows.sort(key=lambda row: (row['last_owner_touch_at'] or '', row.get('name') or ''))
            eligible = len(rows)
            overdue = sum(row['state'] == 'overdue' for row in rows)
            groups.append({'id': rep['rep_owner_id'], 'name': rep['name'],
                           'eligible': eligible, 'overdue': overdue, 'done': eligible - overdue,
                           'pct': round_percent(eligible - overdue, eligible), 'leads': rows})
        total = sum(group['eligible'] for group in groups)
        done = sum(group['done'] for group in groups)
        dashboard[key] = {'hours': hours, 'as_of': as_of.isoformat(),
                          'collected_through': capture['meta']['ended_at'], 'groups': groups,
                          'eligible': total, 'done': done, 'overdue': total - done,
                          'pct': round_percent(done, total), 'target': 95 if hours == 72 else 90,
                          'scope': 'Current lead statuses; displayed lead owners; VP/blank business line; all booking dates'}
    dashboard['scorecard_meta']['followup_as_of'] = as_of.isoformat()
    return dashboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--followup-capture', type=Path)
    parser.add_argument('--process-extract', type=Path)
    args = parser.parse_args()
    dashboard = apply_preview_roster(json.loads(args.baseline.read_text()),
        process_extract=json.loads(args.process_extract.read_text()) if args.process_extract else None)
    if args.capture.exists():
        capture = json.loads(args.capture.read_text())
    else:
        client = CloseClient(load_env_value([args.env_file], 'CLOSE_API_KEY'))
        capture = collect(client, dashboard)
        args.capture.parent.mkdir(parents=True, exist_ok=True)
        args.capture.write_text(json.dumps(capture))
        args.capture.chmod(0o600)
    result = enrich(dashboard, capture)
    if args.followup_capture:
        result = add_followup_snapshot(result, json.loads(args.followup_capture.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result['team_post_call_12h'][k] for k in ('done', 'eligible', 'pending', 'exempt', 'unknown')}))


if __name__ == '__main__':
    main()
