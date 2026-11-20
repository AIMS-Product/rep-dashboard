"""Enrich Rep and the Manager reporting windows from one Close and Avoma capture."""

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time
import json
from pathlib import Path

from avoma_scores import summarize_avoma_scores
from build_adherence_preview import CloseClient, add_adherence_to_dashboard, load_env_value
from collect_scorecard_preview import add_followup_snapshot, cohort_for_rep, collect, enrich
from collect_shared_followup import collect_followup
from dashboard_qa import apply_avoma_scores, capture_avoma_qa
from scorecard_rules import cash_collected
from shared_close_meetings import SharedCloseMeetings
from shared_periods import PACIFIC
from shared_sales_contract import derive_rep, make_rep_base, validate_pair

MANAGER_ADHERENCE = frozenset({"Joe Dysert"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--canonical', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--reuse-enrichment', action='store_true')
    parser.add_argument('--meeting-catalog', type=Path)
    args = parser.parse_args()

    canonical = json.loads(args.canonical.read_text())
    cutoff = datetime.fromisoformat(canonical['cutoff'])
    base = json.loads(args.base.read_text()) if args.base else make_rep_base(canonical)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.reuse_enrichment:
        if canonical.get('weeks'):
            raise ValueError('Weekly views require period-scoped evidence; cached MTD enrichment cannot supply it')
        rep = derive_rep(base, canonical)
    else:
        meetings = SharedCloseMeetings.read(args.meeting_catalog) if args.meeting_catalog else None
        key = load_env_value([], 'CLOSE_API_KEY')
        rep, weeks = build_views(base, canonical, cutoff, meetings, key, args.output.parent)
        checkpoint = args.output.with_name('weekly-reps.private.json')
        checkpoint.write_text(json.dumps(weeks))
        checkpoint.chmod(0o600)

    attach_month_summary(rep, canonical, cutoff)
    parity = validate_pair(rep, canonical['mtd'], canonical)
    args.output.write_text(json.dumps(rep, indent=2) + '\n')
    args.output.with_name('parity.json').write_text(json.dumps(parity) + '\n')
    print(json.dumps(parity))


def build_views(base, canonical, cutoff, meetings, close_key, private_dir):
    weeks = canonical.get('weeks', {})
    sources = (canonical, *weeks.values())
    process_rows = {row['meeting_id']: row for scope in sources for row in scope['scheduled_rows']}
    opportunities = {row['id']: row for scope in sources for row in scope['opportunities']}

    month = cutoff.strftime('%Y-%m')
    extract_path = private_dir / 'precall-extract.private.json'
    union = add_adherence_to_dashboard(
        deepcopy(base), accepted_process_rows=list(process_rows.values()),
        shared_meetings=meetings, month_override=month, as_of=cutoff, audit_extract_path=extract_path,
        manager_adherence=MANAGER_ADHERENCE,
    )
    extract = json.loads(extract_path.read_text())
    post = collect(CloseClient(close_key), union, as_of=cutoff,
                   opportunities=list(opportunities.values()), shared_meetings=meetings)
    post_path = private_dir / 'post-capture.private.json'
    post_path.write_text(json.dumps(post))
    post_path.chmod(0o600)
    followup = collect_followup(close_key, union['reps'], cutoff)
    followup_path = private_dir / 'followup-evidence.private.json'
    followup_path.write_text(json.dumps(followup))
    followup_path.chmod(0o600)

    month_start = cutoff.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    starts = [month_start, *(datetime.combine(date.fromisoformat(key), time.min, PACIFIC) for key in weeks)]
    evaluations, emails = capture_avoma_qa(min(starts), cutoff, private_dir / 'avoma-evidence.private.json')

    def project(scope, start, end):
        dashboard = add_adherence_to_dashboard(
            deepcopy(base), extract=extract, month_override=month, as_of=cutoff,
            accepted_process_rows=scope['scheduled_rows'],
            manager_adherence=MANAGER_ADHERENCE,
        )
        scoped_post = {**post,
                       'cohorts': {rep['rep_owner_id']: cohort_for_rep(rep) for rep in dashboard['reps']},
                       'opportunities': scope['opportunities']}
        dashboard = enrich(derive_rep(dashboard, scope), scoped_post)
        dashboard['scorecard_meta']['preview_only'] = False
        qa, metadata = summarize_avoma_scores(evaluations, start, end, cutoff)
        apply_avoma_scores(dashboard, qa, metadata, emails)
        add_followup_snapshot(dashboard, followup)
        attach_cash_summary(dashboard, scope['opportunities'])
        return dashboard

    monthly = project(canonical, month_start, cutoff)
    weekly = {key: project(scope, datetime.combine(date.fromisoformat(key), time.min, PACIFIC),
                           datetime.fromisoformat(scope['period']['end_exclusive']))
              for key, scope in weeks.items()}
    return monthly, weekly


def attach_month_summary(rep, canonical, cutoff):
    today = [o for o in canonical['opportunities'] if o['date_won'][:10] == cutoff.date().isoformat()]
    grouped = defaultdict(list)
    for opportunity in today:
        name = canonical['user_map'].get(opportunity['user_id'], 'Historical closer')
        grouped[name].append(opportunity['reporting_revenue'])
    rep.update(today_deals=len(today), today_revenue=round(sum(o['reporting_revenue'] for o in today), 2),
               today_closers=[{'name': name, 'deals': len(values), 'total': round(sum(values), 2)}
                              for name, values in grouped.items()],
               day_of_month=cutoff.day, month_label=cutoff.strftime('%B %Y'))
    attach_cash_summary(rep, canonical['opportunities'])


def attach_cash_summary(rep, opportunities):
    amounts = []
    for row in rep['reps']:
        own = [opportunity for opportunity in opportunities if opportunity['user_id'] == row['rep_owner_id']]
        values = [cash_collected(opportunity) for opportunity in own]
        valid = [value for value in values if value is not None]
        row['cash_collected'] = {'amount': round(sum(valid), 2) if valid else None,
                                 'recorded': len(valid), 'missing': len(values) - len(valid),
                                 'opportunities': len(own)}
        amounts.extend(values)
        row.get('adherence', {}).pop('lead_results', None)
    valid = [value for value in amounts if value is not None]
    rep['team_cash_collected'] = {'amount': round(sum(valid), 2) if valid else None,
                                  'recorded': len(valid), 'missing': len(amounts) - len(valid),
                                  'opportunities': len(amounts)}


if __name__ == '__main__':
    main()
