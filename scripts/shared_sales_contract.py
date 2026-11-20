"""Derive the rep's common metrics from the authoritative MTD calculation."""
import copy
from calendar import monthrange
from zoneinfo import ZoneInfo
from datetime import datetime
from scorecard_details import attach_financial_details


def rate(a,b):return round(100*a/b,1) if b else None


def derive_rep(base, canonical):
    result=copy.deepcopy(base)
    mtd=canonical['mtd'];teams={r['value']:r for r in mtd['team']}
    opportunities=canonical['opportunities'];calls=canonical['occurred_rows']
    active={r['name'] for r in canonical['roster']}
    result['reps']=[r for r in result['reps'] if r['name'] in active]
    names={r['name'] for r in result['reps']}
    if names!=active:raise ValueError('Rep roster is missing an authoritative active closer')
    for rep in result['reps']:
        name=rep['name'];r=teams.get(name,{})
        owned=[row for row in calls if row['owner']==name]
        rep.update(booked=len(owned),occurred_calls=len(owned),shown=sum(row['outcome'].strip().lower()=='completed' for row in owned),
                   no_shows=sum(row['outcome'].strip().lower()=='no show' for row in owned),
                   qualified=r.get('qualified',0),deals=r.get('closed_won',0),revenue=r.get('revenue',0))
        rep['scheduled_calls']=r.get('scheduled_calls',len(owned));rep['unresolved_calls']=r.get('unresolved_calls',0)
        rep.update(show_rate=rate(rep['shown'],rep['booked']),close_rate=rate(rep['deals'],rep['booked']),
                   qualification_rate=rate(rep['qualified'],rep['booked']),aov=round(rep['revenue']/rep['deals'],2) if rep['deals'] else None,
                   revenue_per_lead=round(rep['revenue']/rep['booked'],2) if rep['booked'] else None,
                   pct_to_quota=rate(rep['revenue'],rep['quota']) if rep.get('quota') else None)
        for key,bucket in [('marketing','Marketing'),('lane2','Lane 2'),('social_dms','Social DMs')]:rep[key]=sum(row['bucket']==bucket for row in owned)
    historical_rows=[r for r in mtd['team'] if r['value'] not in names]
    historical={key:sum(row.get(source,0) for row in historical_rows) for key,source in
        [('booked','occurred_calls'),('shown','showed'),('no_shows','no_shows'),('qualified','qualified'),
         ('deals','closed_won'),('revenue','revenue'),('scheduled_calls','scheduled_calls'),('unresolved_calls','unresolved_calls')]}
    historical['names']=sorted(row['value'] for row in historical_rows if any(row.get(key,0) for key in ['occurred_calls','closed_won','revenue']))
    historical.update(show_rate=rate(historical['shown'],historical['booked']),close_rate=rate(historical['deals'],historical['booked']),
                      qualification_rate=rate(historical['qualified'],historical['booked']))
    result['historical_closers']=historical
    totals=mtd['totals']
    result.update(total_booked=totals['occurred_calls'],total_occurred_calls=totals['occurred_calls'],total_shown=totals['showed'],
                  total_no_shows=totals['no_shows'],total_scheduled_calls=totals['scheduled_calls'],total_unresolved_calls=totals['unresolved_calls'],
                  total_qualified=totals['qualified'],total_deals=totals['closed_won'],total_revenue=totals['revenue'],closed_grain='opportunity')
    result.update(team_show_rate=rate(result['total_shown'],result['total_booked']),team_close_rate=rate(result['total_deals'],result['total_booked']),
                  team_qualification_rate=rate(result['total_qualified'],result['total_booked']),team_aov=round(result['total_revenue']/result['total_deals'],2) if result['total_deals'] else None,
                  pct_team_quota=rate(result['total_revenue'],result['team_quota']))
    cutoff=datetime.fromisoformat(canonical['cutoff']).astimezone(ZoneInfo('America/Los_Angeles'));result['updated_at']=cutoff.strftime('%Y-%m-%d %H:%M:%S %Z')
    days=monthrange(cutoff.year,cutoff.month)[1]
    elapsed=sum(cutoff.date().replace(day=day).weekday()<5 for day in range(1,cutoff.day+1))
    total=sum(cutoff.date().replace(day=day).weekday()<5 for day in range(1,days+1))
    result.update(day_of_month=cutoff.day,days_in_month=days,working_days_elapsed=elapsed,working_days_total=total,pct_month_passed=round(elapsed/total*100,1))
    result['metric_definitions']={**result.get('metric_definitions',{}),'source':'shared_mtd','cutoff_exclusive':canonical['cutoff'],'rules_version':canonical['rules_version'],'booked':'native_completed_plus_no_show','closed_won':'MTD opportunity/date_won grain; historical wins retained'}
    if result.get('scorecard_meta'):
        result['scorecard_meta']['close_basis']='Shared MTD won opportunities divided by its occurred first calls, including historical closer credit'
        result['scorecard_meta']['cash_basis']='Cash Collected on the shared MTD included won opportunities, grouped by date_won and displayed owner'
    result=attach_financial_details(result,opportunities)
    return result


def validate_pair(rep, mtd, canonical):
    cutoff=datetime.fromisoformat(canonical['cutoff'])
    if cutoff.tzinfo is None:raise ValueError('Reporting cutoff requires a timezone')
    if mtd.get('rules_version')!=canonical['rules_version'] or rep['metric_definitions'].get('rules_version')!=canonical['rules_version']:raise ValueError('Rule version mismatch')
    if canonical['cutoff'] != mtd['source_cutoff']:raise ValueError('Cutoff mismatch')
    if rep['metric_definitions']['cutoff_exclusive']!=canonical['cutoff']:raise ValueError('Rep cutoff mismatch')
    if mtd['booking_scope']!='vp-appointments.v2':raise ValueError('Not the approved MTD VP scope')
    totals=mtd['totals']
    for left,right in [('total_booked','occurred_calls'),('total_shown','showed'),('total_no_shows','no_shows'),('total_deals','closed_won'),('total_qualified','qualified'),('total_revenue','revenue')]:
        if rep[left]!=totals[right]:raise ValueError('Team parity mismatch: '+left)
    calls=canonical['occurred_rows'];opps=canonical['opportunities']
    if len({r['meeting_id'] for r in calls})!=len(calls):raise ValueError('Duplicate accepted meeting')
    if len({o['id'] for o in opps})!=len(opps):raise ValueError('Duplicate won opportunity')
    if len(calls)!=rep['total_booked'] or len(opps)!=rep['total_deals']:raise ValueError('Identity/count mismatch')
    teams={r['value']:r for r in mtd['team']}
    active={r['name'] for r in rep['reps']}
    if active!={r['name'] for r in canonical['roster']}:raise ValueError('Roster mismatch')
    if sum(r.get('closed_won',0) for r in mtd['team'])!=totals['closed_won']:raise ValueError('Historical wins do not reconcile')
    for row in rep['reps']:
        mt=teams.get(row['name'],{})
        for left,right in [('booked','occurred_calls'),('shown','showed'),('deals','closed_won'),('qualified','qualified'),('revenue','revenue')]:
            if row[left]!=mt.get(right,0):raise ValueError(f"Rep parity mismatch: {row['name']}/{left}")
        if row['show_rate']!=rate(row['shown'],row['booked']) or row['close_rate']!=rate(row['deals'],row['booked']):raise ValueError('Rate mismatch')
        if row['booked']!=row['shown']+row['no_shows']:raise ValueError('Outcome denominator mismatch')
    if rep['team_close_rate']!=rate(rep['total_deals'],rep['total_booked']) or rep['team_show_rate']!=rate(rep['total_shown'],rep['total_booked']):raise ValueError('Team rate mismatch')
    for key, source in [('booked','occurred_calls'),('shown','showed'),('no_shows','no_shows'),('qualified','qualified'),('deals','closed_won'),('revenue','revenue')]:
        if rep['historical_closers'][key] != sum(row.get(source,0) for row in mtd['team'] if row['value'] not in active):raise ValueError('Historical aggregate mismatch: '+key)
    historical=[row for row in calls if row['owner'] not in active]
    if len(historical)!=rep['historical_closers']['booked']:raise ValueError('Historical call identity mismatch')
    if sum(r['booked'] for r in rep['reps'])+rep['historical_closers']['booked']!=rep['total_booked']:raise ValueError('Roster plus historical total mismatch')
    if sum(r['shown'] for r in rep['reps'])+rep['historical_closers']['shown']!=rep['total_shown']:raise ValueError('Historical show total mismatch')
    return {'matched':True,'cutoff':canonical['cutoff'],'rules_version':canonical['rules_version'],'calls':len(calls),'wins':len(opps),'reps':len(rep['reps'])}


def make_rep_base(canonical):
    """Use existing display/target configuration without a second CRM calculation."""
    from fetch_data import REP_QUOTAS, MANAGER_USERS, LEAD_USERS, TEAM_QUOTAS, DEFAULT_TEAM_QUOTA
    cutoff=datetime.fromisoformat(canonical['cutoff']).astimezone(ZoneInfo('America/Los_Angeles'))
    users=canonical['user_map'];rows=[]
    for person in canonical['roster']:
        name=person['name'];ids=[uid for uid,value in users.items() if value==name]
        if len(ids)!=1:raise ValueError('Active closer needs one verified Close identity: '+name)
        rows.append({'name':name,'rep_owner_id':ids[0],'quota':REP_QUOTAS.get(name,0),
                     'is_manager':name in MANAGER_USERS,'is_lead':name in LEAD_USERS,'exclude_meetings':False})
    base={'month_label':cutoff.strftime('%B %Y'),'team_quota':TEAM_QUOTAS.get((cutoff.year,cutoff.month),DEFAULT_TEAM_QUOTA),'reps':rows}
    result=derive_rep(base,canonical);result['reps'].sort(key=lambda row:row['revenue'],reverse=True)
    return result
