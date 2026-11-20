"""Run the current MTD authority in its own process; private rows never publish."""
import argparse,json,sys,tempfile,hashlib,re
from pathlib import Path
from datetime import datetime,timedelta


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
    reader=ReportingReader(CloseReader(load_api_key()).get,cutoff.date().replace(day=1),cutoff)
    raw=build_dataset(reader,cutoff,cutoff.date().replace(day=1));scheduled=scheduled_dataset(reader);occurred=occurred_dataset(scheduled)
    if 'won_rows' not in raw:raise ValueError('MTD authority needs the shared snapshot interface')
    args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        source=Path(temp)/'raw.json';source.write_text(json.dumps(raw));mtd=apply_occurred_mtd(public_data(source),occurred,scheduled)
    mtd['view']={'key':cutoff.strftime('%Y-%m'),'kind':'month','month':cutoff.strftime('%Y-%m'),'start':cutoff.date().replace(day=1).isoformat(),'end':cutoff.date().isoformat(),'end_exclusive':(cutoff.date().replace(day=28)+timedelta(days=4)).replace(day=1).isoformat(),'frozen':False,'label':cutoff.strftime('%B %Y')}
    mtd['metric_version']='calls-occurred.v1';mtd['source_cutoff']=cutoff.isoformat()
    # Match every included opportunity by ID; never reconstruct money from lead IDs.
    opportunity_rows=list(reader.pages('opportunity/',{'status_type':'won','date_won__gte':cutoff.date().replace(day=1).isoformat(),'date_won__lte':cutoff.date().isoformat(),
        '_fields':'id,lead_id,lead_name,user_id,date_won,pipeline_id,custom.cf_JP6Zdnv1ClODfctK5iwY9XseSeXEE9ZXbyopPw45OqZ'}))
    by_id={o['id']:o for o in opportunity_rows};opps=[]
    for row in raw['won_rows']:
        oid=row['opportunity_id']
        if oid not in by_id:raise ValueError('Included MTD win missing from cash evidence')
        o={**by_id[oid],'lead_name':row['lead_name'],'reporting_revenue':row['revenue']}
        if o['lead_id']!=row['lead_id'] or o['user_id']!=row['owner']:raise ValueError('Won evidence identity changed during read')
        opps.append(o)
    versions={name:hashlib.sha256((args.mtd_root/'v2'/name).read_bytes()).hexdigest() for name in ['build.py','aggregates.py','outcome_core.py','outcome_public.py','rules.json']}
    canonical={'source_versions':{'mtd':versions},'cutoff':cutoff.isoformat(),'rules_version':raw['rules_version'],'roster':roster,'mtd':mtd,'scheduled_rows':scheduled['rows'],'occurred_rows':occurred['rows'],'opportunities':opps,'user_map':reader.user_map}
    (args.output/'canonical.json').write_text(json.dumps(canonical,indent=2)+'\n');(args.output/'canonical.json').chmod(0o600)
    (args.output/'mtd.json').write_text(json.dumps(mtd,indent=2)+'\n')
    render_page(mtd,args.mtd_root,args.output/'mtd.html')
    print(json.dumps({'cutoff':cutoff.isoformat(),'calls':len(occurred['rows']),'wins':len(opps)}))

if __name__=='__main__':main()
