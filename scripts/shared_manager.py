"""Project the manager presentation from the same accepted rep/MTD records."""
from copy import deepcopy
from collections import Counter
from scorecard_rules import post_call_average
from shared_sales_contract import rate, validate_pair


def call_detail(row):
    return {'lead_id':row['lead_id'],'meeting_id':row['meeting_id'],'name':row.get('lead_name',row['lead_id']),
            'date':row.get('scheduled_date_pt',''),'meeting_outcome':row.get('outcome',''),
            'meeting_status':row.get('status',''),'funnel':row.get('channel','')}


def derive_manager(base, rep, canonical, source_map):
    result=deepcopy(base);old={row['name']:row for row in base['reps']};rows=[]
    accepted={row['meeting_id'] for row in canonical['occurred_rows']}
    for source in rep['reps']:
        row={**deepcopy(old.get(source['name'],{})),**deepcopy(source)}
        row.setdefault('lane',1)
        calls=[r for r in canonical['occurred_rows'] if r['owner']==row['name']]
        unresolved=[r for r in canonical.get('scheduled_rows',[]) if r['owner']==row['name'] and r['meeting_id'] not in accepted]
        row['metric_details']={
            'booked':[call_detail(r) for r in calls],
            'shown':[call_detail(r) for r in calls if r['outcome'].lower()=='completed'],
            'no_shows':[call_detail(r) for r in calls if r['outcome'].lower()=='no show'],
            'qualified':[call_detail(r) for r in calls if r.get('qualified_contribution')],
            'no_outcome':[call_detail(r) for r in unresolved], 'upcoming':[],
            'deals':[{'lead_id':o['lead_id'],'opportunity_id':o['id'],'name':o.get('lead_name',o['lead_id']),
                      'date':o.get('date_won',''),'value':o['reporting_revenue']} for o in canonical['opportunities'] if o['user_id']==row['rep_owner_id']]}
        row['avg_rev_per_deal']=row['aov'];row['pre_call_adherence']=row.get('adherence',{}).get('pre_call_pct')
        row['post_call_adherence']=row.get('post_call_12h',{}).get('pct')
        rows.append(row)
    result.update(reps=rows,**{k:deepcopy(v) for k,v in rep.items() if k.startswith(('total_','team_'))})
    result.update(updated_at=rep['updated_at'],today_str=canonical['cutoff'][:10],week_label=rep['month_label']+' · Month to date',
                  metric_definitions={**rep['metric_definitions'],'period_kind':'month'},historical_closers=deepcopy(rep['historical_closers']),
                  scorecard_meta=deepcopy(rep.get('scorecard_meta',{})),qa_score_meta=deepcopy(rep.get('qa_score_meta',{})),
                  adherence_meta=deepcopy(rep.get('adherence_meta',{})))
    h=result['historical_closers'];h['occurred_calls']=h['booked']
    result['team_avg_rev_per_deal']=rep.get('team_aov')
    active=[r for r in rows if not r.get('is_manager')]
    def pre_average(group):
        values=[]
        for key in ['loom_usage','precall_text','day_of_confirmation_text']:
            cells=[r.get('adherence',{}).get('steps',{}).get(key,{}) for r in group]
            done=sum(c.get('done',0) for c in cells);eligible=sum(c.get('eligible',0) for c in cells)
            if eligible:values.append(int(100*done/eligible+0.5))
        return int(sum(values)/len(values)+0.5) if values else None
    result['team_pre_call_adherence']=pre_average(active)
    result['lane_pre_call_adherence']={str(lane):pre_average([r for r in active if r['lane']==lane]) for lane in [1,2]}

    def post_average(group):
        scores = [r.get('post_call_12h', {}) for r in group]
        return post_call_average(
            sum(score.get('recap_done', 0) for score in scores),
            sum(score.get('next_step_done', 0) for score in scores),
            sum(score.get('eligible', 0) for score in scores),
        )

    result['team_post_call_adherence'] = post_average([r for r in active if r['lane'] in (1, 2)])
    result['lane_post_call_adherence'] = {
        str(lane): post_average([r for r in active if r['lane'] == lane]) for lane in (1, 2)
    }
    tasks=[r for r in active if r.get('tasks_incomplete') is not None]
    incomplete=sum(r['tasks_incomplete'] for r in tasks);overdue=sum(r.get('tasks_overdue',0) for r in tasks)
    result.update(team_overdue=overdue,team_task_adherence=round(100*(incomplete-overdue)/incomplete,1) if incomplete else 100.0)
    funnels=[{'funnel':r['channel'],'count':r['occurred_calls']} for r in canonical['mtd'].get('channels',[]) if r['occurred_calls']]
    counts=Counter()
    for f in funnels:counts[{'In-House':'in_house','External':'external'}.get(source_map.get(f['funnel']),'unknown')]+=f['count']
    result.update(funnel_breakdown=funnels,funnel_sources={**counts,**{k+'_pct':rate(counts[k],rep['total_booked']) for k in ['in_house','external','unknown']}})
    result['num_reps']=len(active)
    result['team_open_leads']=sum(r.get('open_leads',0) or 0 for r in active)
    result['team_est_pipeline']=round(result['team_open_leads']*8000*0.30)
    result['team_targets']={k:(v*len(active) if k in {'booked','shown','qualified','deals','revenue'} and v is not None else v) for k,v in result.get('targets',{}).items()}
    return result


def validate_manager(manager,rep,canonical):
    validate_pair(manager,canonical['mtd'],canonical)
    for key in ['total_scheduled_calls','total_unresolved_calls','total_qualified','total_no_shows','team_qa_score','team_qa_score_count','team_post_call_12h']:
        if manager.get(key)!=rep.get(key):raise ValueError('Manager shared metric mismatch: '+key)
    by_name={r['name']:r for r in rep['reps']}
    for row in manager['reps']:
        original=by_name[row['name']]
        for key in ['adherence','post_call_12h','qa_score','qa_score_count']:
            if row.get(key)!=original.get(key):raise ValueError('Manager scorecard mismatch: '+key)
        for key,count in [('booked',row['booked']),('shown',row['shown']),('no_shows',row['no_shows']),('qualified',row['qualified']),('deals',row['deals']),('no_outcome',row['unresolved_calls'])]:
            if len(row['metric_details'][key])!=count:raise ValueError('Manager drill-down mismatch: '+key)
    if sum(r['count'] for r in manager['funnel_breakdown'])!=rep['total_booked']:raise ValueError('Manager funnel mismatch')
    return True
