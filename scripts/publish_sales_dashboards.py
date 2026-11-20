#!/usr/bin/env python3
"""Build, validate and stage all three dashboards; publication is an explicit operation."""
import argparse,json,os,subprocess,sys,tempfile,shutil,hashlib
from contextlib import nullcontext
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from shared_sales_release import stage_release,accept_release,rollback_release
from build_adherence_preview import load_env_value

SCRIPTS=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    build=sub.add_parser('build');build.add_argument('--mtd-root',type=Path,required=True);build.add_argument('--manager-root',type=Path,required=True);build.add_argument('--manager-base',type=Path,required=True);build.add_argument('--output',type=Path,required=True)
    build.add_argument('--base',type=Path);build.add_argument('--reuse-enrichment',action='store_true');build.add_argument('--env-file',type=Path)
    build.add_argument('--cutoff',default=None);build.add_argument('--private-work-dir',type=Path);build.add_argument('--canonical',type=Path)
    accept=sub.add_parser('accept');accept.add_argument('--stage',type=Path,required=True);accept.add_argument('--site-root',type=Path,required=True)
    rollback=sub.add_parser('rollback');rollback.add_argument('--site-root',type=Path,required=True);rollback.add_argument('--release-id',required=True)
    args=parser.parse_args()
    if args.command=='accept':print(json.dumps(accept_release(args.stage,args.site_root)));return
    if args.command=='rollback':print(json.dumps(rollback_release(args.site_root,args.release_id)));return
    cutoff=datetime.fromisoformat(args.cutoff) if args.cutoff else datetime.now(ZoneInfo('America/Los_Angeles'))
    if cutoff.tzinfo is None:raise ValueError('Cutoff must have timezone')
    cutoff=cutoff.astimezone(ZoneInfo('America/Los_Angeles'))
    if cutoff>datetime.now(cutoff.tzinfo):raise ValueError('Future cutoff is not permitted')
    env=os.environ.copy()
    for key in ['CLOSE_API_KEY','AVOMA_API_KEY']:
        value=load_env_value([args.env_file] if args.env_file else [],key)
        if value:env[key]=value
    mtd_env={key:value for key,value in env.items() if key!='AVOMA_API_KEY'}
    base=json.loads(args.base.read_text()) if args.base else None
    if base is None:
        from fetch_data import REP_QUOTAS, MANAGER_USERS
        roster=[{'name':name} for name in sorted(set(REP_QUOTAS) | MANAGER_USERS)]
    else:roster=[{'name':r['name']} for r in base['reps'] if not r.get('exclude_meetings')]
    if args.private_work_dir:
        args.private_work_dir.mkdir(parents=True,exist_ok=True);args.private_work_dir.chmod(0o700)
        if args.output.resolve().is_relative_to(args.private_work_dir.resolve()) or args.private_work_dir.resolve().is_relative_to(args.output.resolve()):raise ValueError('Private inputs and public stage must be separate')
    context=nullcontext(str(args.private_work_dir)) if args.private_work_dir else tempfile.TemporaryDirectory(prefix='shared-sales-private-')
    manager_version=hashlib.sha256((args.manager_root/'dashboard-template.html').read_bytes()).hexdigest()
    manager_sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.manager_root/'scripts').glob('*.py')}
    versions={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SCRIPTS.glob('*.py')}
    with context as private:
        private=Path(private);roster_path=private/'roster.json';roster_path.write_text(json.dumps(roster))
        if args.canonical:
            for name in ['canonical.json','mtd.html']:
                source=args.canonical.parent/name
                if source.resolve()!=(private/name).resolve():shutil.copyfile(source,private/name)
            subprocess.run([sys.executable,str(SCRIPTS/'build_shared_mtd.py'),'--mtd-root',str(args.mtd_root),'--roster',str(roster_path),'--cutoff',cutoff.isoformat(),'--output',str(private),'--render-only'],check=True,env=mtd_env)
        else:
            subprocess.run([sys.executable,str(SCRIPTS/'build_shared_mtd.py'),'--mtd-root',str(args.mtd_root),'--roster',str(roster_path),'--cutoff',cutoff.isoformat(),'--output',str(private)],check=True,env=mtd_env)
        cmd=[sys.executable,str(SCRIPTS/'build_shared_rep.py'),'--canonical',str(private/'canonical.json'),'--output',str(private/'rep.json')]
        if args.base:cmd+=['--base',str(args.base)]
        if args.reuse_enrichment:cmd+=['--reuse-enrichment']
        subprocess.run(cmd,check=True,env=env)
        cmd=[sys.executable,str(SCRIPTS/'build_shared_manager.py'),'--manager-root',str(args.manager_root),'--canonical',str(private/'canonical.json'),'--rep',str(private/'rep.json'),'--base',str(args.manager_base),'--output',str(private)]
        if args.reuse_enrichment:cmd+=['--reuse-enrichment']
        subprocess.run(cmd,check=True,env=env)
        canonical=json.loads((private/'canonical.json').read_text());rep=json.loads((private/'rep.json').read_text())
        after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SCRIPTS.glob('*.py')}
        if manager_version!=hashlib.sha256((args.manager_root/'dashboard-template.html').read_bytes()).hexdigest():raise ValueError('Manager source changed during build')
        if manager_sources!={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.manager_root/'scripts').glob('*.py')}:raise ValueError('Manager operating source changed during build')
        if after!=versions:raise ValueError('Reporting source changed during the build')
        canonical.setdefault('source_versions',{})['rep']=versions
        canonical['source_versions']['manager_template']=manager_version
        canonical['source_versions']['manager_operating']=manager_sources
        canonical['source_versions']['rep_template']=hashlib.sha256((SCRIPTS.parent/'index.html').read_bytes()).hexdigest()
        manifest=stage_release(rep,canonical['mtd'],canonical,(private/'mtd.html').read_text(),args.output,manager=json.loads((private/'manager.json').read_text()),manager_html=(private/'manager.html').read_text())
        print(json.dumps({'release_id':manifest['release_id'],'cutoff':manifest['cutoff'],'parity':manifest['parity']}))

if __name__=='__main__':main()
