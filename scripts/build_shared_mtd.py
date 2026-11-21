"""Run the current MTD authority in its own process; private rows never publish."""
import argparse,json,sys,tempfile,hashlib,re
from pathlib import Path
from datetime import datetime,time,timedelta
from shared_periods import PACIFIC, scheduled_between, week_metadata, week_starts, weekly_canonical


def certified_vp_inputs(path, digest, cutoff, month_start):
    if path is None and digest is None:
        return {}
    if path is None or digest is None:
        raise ValueError('VP meeting input requires the private artifact and its approved digest')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError('VP meeting artifact hash differs from the approved input')
    bundle = json.loads(payload)
    if (bundle['contract'] != 'steeltrap.vp-equivalent-bookings.v1'
            or datetime.fromisoformat(bundle['source_cutoff']) > cutoff
            or not bundle['steeltrap_publication_id']):
        raise ValueError('VP booking artifact belongs to a different accepted source cutoff')
    month = month_start.strftime('%Y-%m')
    if month not in bundle['months']:
        raise ValueError('The approved SteelTrap list does not cover this MTD report month')
    return dict(certified_vp_membership=bundle['months'][month],
                reviewed_booking_origins=bundle['reviewed_booking_origins'])


def verify_vp_rows(raw, expected):
    from collections import Counter

    actual = [{
        'activity_id': row['meeting_id'],
        'booked_day': row['booked_date_pt'],
        'bucket': row['bucket'],
        'appointment_closer': row['appointment_closer'],
    } for row in raw['rows'] if row['bucket'] in {'Lane 2', 'Social DMs'}]
    order = lambda row: (row['booked_day'], row['bucket'], row['activity_id'])
    actual = sorted(actual, key=order)
    if actual != sorted(expected, key=order):
        raise ValueError('MTDv2 meeting IDs, booking dates, buckets or closers differ from the approved list')
    counts = Counter(row['bucket'] for row in actual)
    if (raw['totals']['lane2'], raw['totals']['social_dms']) != (counts['Lane 2'], counts['Social DMs']):
        raise ValueError('MTDv2 booking rollups do not equal the approved meetings')
    return actual


def render_page(mtd,mtd_root,output):
    from publish_shadow import render_report
    navigation=json.loads((mtd_root/'v2/archives/nav.json').read_text())
    render_report(mtd,output,navigation)
    output.write_text(re.sub(r"([\"'])/mtd-funnel-dashboard/",r"\1https://aims-product.github.io/mtd-funnel-dashboard/",output.read_text()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mtd-root',type=Path,required=True);parser.add_argument('--roster',type=Path,required=True)
    parser.add_argument('--cutoff',required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--render-only',action='store_true')
    parser.add_argument('--meeting-catalog', type=Path)
    parser.add_argument('--monthly-only', action='store_true')
    parser.add_argument('--vp-membership-bundle', type=Path)
    parser.add_argument('--vp-membership-sha256')
    parser.add_argument('--read-certified-steeltrap-vp', action='store_true')
    args=parser.parse_args();sys.path.insert(0,str(args.mtd_root/'v2'))
    from build import CloseReader,build_dataset,load_api_key
    from outcome_core import ReportingReader,scheduled_dataset,occurred_dataset
    from outcome_public import apply_occurred_mtd
    from publish_shadow import public_data
    from aggregates import VP_BOOKING_CLOSERS
    if args.render_only:
        canonical=json.loads((args.output/'canonical.json').read_text())
        render_page(canonical['mtd'],args.mtd_root,args.output/'mtd.html');return
    cutoff=datetime.fromisoformat(args.cutoff)
    if cutoff.tzinfo is None:raise ValueError('Cutoff requires timezone')
    roster=json.loads(args.roster.read_text());names=[r['name'] for r in roster]
    if not names or len(set(names))!=len(names) or not set(names)<=set(VP_BOOKING_CLOSERS):raise ValueError('Invalid active roster')
    cutoff = cutoff.astimezone(PACIFIC)
    month_start = cutoff.date().replace(day=1)
    weeks = () if args.monthly_only else week_starts(cutoff)
    earliest = min((month_start, *weeks))
    reader = ReportingReader(CloseReader(load_api_key()).get, earliest, cutoff)
    vp_inputs = certified_vp_inputs(args.vp_membership_bundle, args.vp_membership_sha256, cutoff, month_start)
    vp_membership_sha256 = args.vp_membership_sha256
    if args.read_certified_steeltrap_vp:
        if vp_inputs:
            raise ValueError('Supply exactly one certified VP booking source')
        from read_steeltrap_vp_membership import read_accepted_bundle
        bundle = read_accepted_bundle()
        if datetime.fromisoformat(bundle['source_cutoff']) != cutoff:
            raise ValueError('The requested cutoff differs from the accepted SteelTrap VP generation')
        vp_inputs = dict(certified_vp_membership=bundle['months'][month_start.strftime('%Y-%m')],
                         reviewed_booking_origins=bundle['reviewed_booking_origins'])
        vp_membership_sha256=hashlib.sha256(json.dumps(bundle,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    raw = build_dataset(reader, cutoff, month_start, **vp_inputs)
    vp_booking_source = None
    if args.vp_membership_bundle:
        approved = json.loads(args.vp_membership_bundle.read_bytes())
        verified = verify_vp_rows(raw, vp_inputs['certified_vp_membership'])
        vp_booking_source = {
            'steeltrap_publication_id': approved['steeltrap_publication_id'],
            'source_cutoff': approved['source_cutoff'],
            'meeting_count': len(verified),
            'membership_sha256': hashlib.sha256(json.dumps(
                verified, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        }
    scheduled_all = scheduled_dataset(reader)
    scheduled = scheduled_between(scheduled_all, month_start, cutoff)
    occurred = occurred_dataset(scheduled)
    if 'won_rows' not in raw:raise ValueError('MTD authority needs the shared snapshot interface')
    args.output.mkdir(parents=True,exist_ok=True)
    if args.meeting_catalog:
        # Pass only the projected meeting fields to Rep through the private working directory.
        with tempfile.NamedTemporaryFile(mode='w', dir=args.meeting_catalog.parent, delete=False) as catalog:
            json.dump(reader.meetings, catalog, separators=(',', ':'))
        Path(catalog.name).replace(args.meeting_catalog)
    with tempfile.TemporaryDirectory() as temp:
        source=Path(temp)/'raw.json';source.write_text(json.dumps(raw));mtd=apply_occurred_mtd(public_data(source),occurred,scheduled)
    mtd['view']={'key':cutoff.strftime('%Y-%m'),'kind':'month','month':cutoff.strftime('%Y-%m'),'start':cutoff.date().replace(day=1).isoformat(),'end':cutoff.date().isoformat(),'end_exclusive':(cutoff.date().replace(day=28)+timedelta(days=4)).replace(day=1).isoformat(),'frozen':False,'label':cutoff.strftime('%B %Y')}
    mtd['metric_version']='calls-occurred.v1';mtd['source_cutoff']=cutoff.isoformat()
    if vp_booking_source:
        mtd['vp_booking_source'] = vp_booking_source
    # Wins are independent of meetings. Reuse the existing authority for the
    # previous-month tail when a supported week extends before this month.
    raw_wins = raw['won_rows']
    if earliest < month_start:
        start_of_month = datetime.combine(month_start, time.min, PACIFIC)
        earlier = build_dataset(reader, start_of_month, earliest)
        raw_wins = earlier['won_rows'] + raw_wins
    opportunities = cash_evidence(reader, raw_wins, earliest, cutoff)
    monthly_ids = {row['opportunity_id'] for row in raw['won_rows']}
    opps = [row for row in opportunities if row['id'] in monthly_ids]
    versions={name:hashlib.sha256((args.mtd_root/'v2'/name).read_bytes()).hexdigest() for name in ['build.py','aggregates.py','outcome_core.py','outcome_public.py','rules.json']}
    if vp_booking_source:
        versions['vp_booking_source'] = vp_booking_source
    if vp_inputs:
        versions['certified_vp_bookings.py']=hashlib.sha256((args.mtd_root/'v2/certified_vp_bookings.py').read_bytes()).hexdigest()
        versions['vp_membership_sha256']=vp_membership_sha256
    canonical={'source_versions':{'mtd':versions},'cutoff':cutoff.isoformat(),'rules_version':raw['rules_version'],'roster':roster,'mtd':mtd,'scheduled_rows':scheduled['rows'],'occurred_rows':occurred['rows'],'opportunities':opps,'user_map':reader.user_map}
    canonical['weeks'] = {}
    for start in weeks:
        period = week_metadata(start, cutoff)
        week_scheduled = scheduled_between(scheduled_all, start, datetime.fromisoformat(period['end_exclusive']))
        week_occurred = occurred_dataset(week_scheduled)
        canonical['weeks'][period['key']] = weekly_canonical(
            period, week_scheduled, week_occurred, opportunities,
            user_map=reader.user_map, roster=roster, cutoff=cutoff,
            rules_version=raw['rules_version'],
        )
    (args.output/'canonical.json').write_text(json.dumps(canonical,indent=2)+'\n');(args.output/'canonical.json').chmod(0o600)
    (args.output/'mtd.json').write_text(json.dumps(mtd,indent=2)+'\n')
    render_page(mtd,args.mtd_root,args.output/'mtd.html')
    print(json.dumps({'cutoff':cutoff.isoformat(),'calls':len(occurred['rows']),'wins':len(opps)}))

def cash_evidence(reader, raw_wins, start, cutoff):
    opportunity_rows = reader.pages('opportunity/', {
        'status_type': 'won', 'date_won__gte': start.isoformat(),
        'date_won__lte': (cutoff - timedelta(microseconds=1)).date().isoformat(),
        '_fields': 'id,lead_id,lead_name,user_id,date_won,pipeline_id,custom.cf_JP6Zdnv1ClODfctK5iwY9XseSeXEE9ZXbyopPw45OqZ',
    })
    by_id = {row['id']: row for row in opportunity_rows}
    result = []
    for row in raw_wins:
        opportunity = by_id.get(row['opportunity_id'])
        if opportunity is None:
            raise ValueError('Included win missing from cash evidence')
        if opportunity['lead_id'] != row['lead_id'] or opportunity['user_id'] != row['owner']:
            raise ValueError('Won evidence identity changed during read')
        result.append({**opportunity, 'lead_name':row['lead_name'], 'reporting_revenue':row['revenue']})
    return result


if __name__=='__main__':main()
