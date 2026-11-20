"""Collect manager-only operating data without repeating call, win or QA calculation."""
import argparse,importlib.util,json,sys
from datetime import datetime
from pathlib import Path
from shared_manager import derive_manager,validate_manager
from shared_manager_template import local_manager


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manager-root',type=Path,required=True);p.add_argument('--canonical',type=Path,required=True);p.add_argument('--rep',type=Path,required=True);p.add_argument('--base',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--reuse-enrichment',action='store_true');args=p.parse_args()
    canonical=json.loads(args.canonical.read_text());rep=json.loads(args.rep.read_text());base=json.loads(args.base.read_text())
    # Import the existing manager adapter in isolation; only its operating-data functions run.
    sys.path.insert(0,str(args.manager_root/'scripts'))
    spec=importlib.util.spec_from_file_location('manager_operating_data',args.manager_root/'scripts/fetch_data.py');native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
    if not args.reuse_enrichment:
        native.init_session();reads=[];get=native.api_get
        request_get=native.session.get
        def bounded_get(*args,**kwargs):
            kwargs.setdefault('timeout',45);return request_get(*args,**kwargs)
        native.session.get=bounded_get
        class UnverifiedRead(BaseException):pass
        def strict_get(endpoint,params=None):
            try:response=get(endpoint,params)
            except Exception as e:raise UnverifiedRead('Manager operating evidence read failed') from e
            reads.append({'endpoint':endpoint,'params':params,'response':response});return response
        native.api_get=strict_get
        current={r['name'] for r in rep['reps']};native.REP_QUOTAS={k:v for k,v in native.REP_QUOTAS.items() if k in current}
        task,overdue,incomplete=native.fetch_task_adherence(canonical['user_map'],canonical['cutoff'][:10])
        pipeline=native.fetch_open_leads_per_rep(canonical['user_map'])
        base['reps']=[r for r in base['reps'] if r['name'] in current]
        for r in base['reps']:
            if not r.get('is_manager'):
                if r['name'] not in task or r['name'] not in pipeline:raise ValueError('Incomplete manager operating evidence')
                r.update(task_adherence=task[r['name']],tasks_overdue=overdue[r['name']],tasks_incomplete=incomplete[r['name']],open_leads=pipeline[r['name']])
        paginate=native.api_paginate
        def operating_pages(endpoint,params=None):
            for row in paginate(endpoint,params):
                if endpoint=='/status/opportunity/' and not (row.get('type')=='active' and any(k in str(row.get('label') or row.get('name') or '').casefold() for k in ['hot','warm','general'])):continue
                yield row
        native.api_paginate=operating_pages
        native.fetch_opportunity_statuses(base)
        base['auxiliary_snapshot']=datetime.now().astimezone().isoformat()
        evidence=args.output/'manager-operating.private.json';evidence.write_text(json.dumps({'as_of':base['auxiliary_snapshot'],'reads':reads}));evidence.chmod(0o600)
    # Preserve the existing manager goal configuration independently of cached payloads.
    from copy import deepcopy
    base['targets']=deepcopy(native.WEEKLY_TARGETS)
    base['lane_2_targets']=deepcopy(native.LANE_2_TARGETS)
    result=derive_manager(base,rep,canonical,native.FUNNEL_SOURCE);validate_manager(result,rep,canonical)
    result['auxiliary_snapshot']=base.get('auxiliary_snapshot',base.get('updated_at'))
    (args.output/'manager.json').write_text(json.dumps(result,indent=2)+'\n')
    (args.output/'manager.html').write_text(local_manager((args.manager_root/'dashboard-template.html').read_text(),result))

if __name__=='__main__':main()
