"""Enrich a rep view whose financial/call metrics come exclusively from MTD."""
import argparse,json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from shared_sales_contract import derive_rep,validate_pair,make_rep_base
from collect_scorecard_preview import collect,enrich,add_followup_snapshot
from collect_shared_followup import collect_followup
from build_adherence_preview import CloseClient,add_adherence_to_dashboard,LATEST_BOOKED_DATE_FIELD,load_env_value
from dashboard_qa import add_avoma_scores_to_dashboard


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--canonical',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--base',type=Path);parser.add_argument('--reuse-enrichment',action='store_true')
    args=parser.parse_args();canonical=json.loads(args.canonical.read_text());cutoff=datetime.fromisoformat(canonical['cutoff'])
    if args.base:base=json.loads(args.base.read_text())
    else:
        base=make_rep_base(canonical)
    if not args.reuse_enrichment:
        base=add_adherence_to_dashboard(base,preview_only=False,candidate_booked_date_field=LATEST_BOOKED_DATE_FIELD,include_canceled_by_lead_status=True,as_of=cutoff,audit_extract_path=args.output.with_name('precall-extract.private.json'))
        base=add_avoma_scores_to_dashboard(base,as_of=cutoff,audit_path=args.output.with_name('avoma-evidence.private.json'))
    rep=derive_rep(base,canonical)
    if not args.reuse_enrichment:
        key=load_env_value([], 'CLOSE_API_KEY');client=CloseClient(key)
        capture=collect(client,rep,as_of=cutoff,opportunities=canonical['opportunities']);capture['opportunities']=canonical['opportunities']
        checkpoint=args.output.with_name('post-capture.private.json');checkpoint.write_text(json.dumps(capture));checkpoint.chmod(0o600)
        rep=enrich(rep,capture)
        checkpoint=args.output.with_name('rep-enrichment.private.json');checkpoint.write_text(json.dumps(rep));checkpoint.chmod(0o600)
        followup=collect_followup(key,rep['reps'],cutoff)
        checkpoint=args.output.with_name('followup-evidence.private.json');checkpoint.write_text(json.dumps(followup));checkpoint.chmod(0o600)
        rep=add_followup_snapshot(rep,followup)
        rep['scorecard_meta']['preview_only']=False
    # Enrichment must not override authoritative shared metrics.
    rep=derive_rep(rep,canonical)
    today=[o for o in canonical['opportunities'] if str(o.get('date_won'))[:10]==cutoff.date().isoformat()]
    rep['today_deals']=len(today);rep['today_revenue']=round(sum(o['reporting_revenue'] for o in today),2)
    grouped=defaultdict(list)
    for o in today:grouped[canonical['user_map'].get(o['user_id'],'Historical closer')].append(o['reporting_revenue'])
    rep['today_closers']=[{'name':name,'deals':len(values),'total':round(sum(values),2)} for name,values in grouped.items()]
    rep['day_of_month']=cutoff.day;rep['month_label']=cutoff.strftime('%B %Y')
    # Cash detail uses the same included MTD wins, including duplicate leads across opportunities.
    amounts=[]
    from scorecard_rules import cash_collected
    for row in rep['reps']:
        own=[o for o in canonical['opportunities'] if o['user_id']==row['rep_owner_id']];values=[cash_collected(o) for o in own];valid=[v for v in values if v is not None]
        row['cash_collected']={'amount':round(sum(valid),2) if valid else None,'recorded':len(valid),'missing':len(values)-len(valid),'opportunities':len(values)}
        amounts.extend(values);row.get('adherence',{}).pop('lead_results',None)
    valid=[v for v in amounts if v is not None]
    rep['team_cash_collected']={'amount':round(sum(valid),2) if valid else None,'recorded':len(valid),'missing':len(amounts)-len(valid),'opportunities':len(amounts)}
    parity=validate_pair(rep,canonical['mtd'],canonical)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(rep,indent=2)+'\n')
    args.output.with_name('parity.json').write_text(json.dumps(parity,indent=2)+'\n');print(json.dumps(parity))

if __name__=='__main__':main()
