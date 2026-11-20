"""Validated, immutable three-view releases with one atomic accepted pointer."""
import copy,fcntl,hashlib,json,os,re,shutil,tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from shared_sales_contract import validate_pair
from shared_manager import validate_manager

FILES=('rep.json','mtd.json','mtd.html')
MANAGER_FILES=('manager.json','manager.html')
FORBIDDEN={'activities','contacts','body_text','body_html','authorization','api_key','custom','raw_response'}


def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def digest(value):return hashlib.sha256(value).hexdigest()

def check_public(value):
    if isinstance(value,dict):
        if set(k.lower() for k in value)&FORBIDDEN:raise ValueError('Private input cannot be published')
        for child in value.values():check_public(child)
    elif isinstance(value,list):
        for child in value:check_public(child)


def check_mtd_html(html,mtd):
    match=re.search(r'<script id="v2-data" type="application/json">(.*?)</script>',html,re.S)
    if not match:raise ValueError('MTD HTML has no aggregate evidence')
    embedded=json.loads(match.group(1))
    for key in ['totals','team','channels','setters','source_cutoff','rules_version','booking_scope']:
        if embedded.get(key)!=mtd.get(key):raise ValueError('MTD HTML does not match shared data: '+key)



def check_manager_html(html,manager):
    match=re.search(r'<script type="application/json" id="shared-dashboard-data">(.*?)</script>',html or '',re.S)
    if not match or json.loads(match.group(1))!=manager:raise ValueError('Manager HTML does not match shared data')



def with_shared_refresh(html,release_id,surface):
    """Preserve automatic refresh after a viewer opens its verified full-page release."""
    script='''<script data-shared-refresh>
(function(){
 const shownRelease=RELEASE_ID, file=SURFACE_FILE;
 if(!location.pathname.includes('/shared/releases/'))return;
 setInterval(async()=>{
  try{
   const current=new URL('../../current.json',location.href);
   const response=await fetch(current,{cache:'no-store'});
   if(!response.ok)throw new Error('Shared refresh unavailable');
   const next=await response.json();
   if(next.schema_version!==2||!/^[a-f0-9]{24}$/.test(next.release_id)||!next.files?.[file])throw new Error('Invalid shared refresh');
   if(next.release_id===shownRelease)return;
   const target=new URL('releases/'+next.release_id+'/'+file,current);
   const page=await fetch(target);
   if(!page.ok)throw new Error('Shared refresh page unavailable');
   const bytes=await page.arrayBuffer();
   const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');
   if(hash!==next.files[file])throw new Error('Shared refresh verification failed');
   location.replace(target.href);
  }catch(error){console.warn('Shared refresh could not be verified; keeping the current report.');}
 },5*60*1000);
})();
</script>'''
    script=script.replace('RELEASE_ID',json.dumps(release_id)).replace('SURFACE_FILE',json.dumps(surface+'.html'))
    head,separator,tail=html.rpartition('</body>')
    if not separator:raise ValueError('Shared page has no body')
    return head+script+separator+tail

def stage_release(rep,mtd,canonical,mtd_html,stage,*,manager=None,manager_html=None):
    parity=validate_pair(rep,mtd,canonical);check_public(rep);check_public(mtd);check_mtd_html(mtd_html,mtd)
    if manager is not None:
        validate_manager(manager,rep,canonical);check_public(manager);check_manager_html(manager_html,manager)
    identity={'rep':rep,'mtd':mtd,'html':mtd_html,'source_versions':canonical.get('source_versions',{})}
    if manager is not None:identity.update(manager=manager,manager_html=manager_html)
    release_id=digest(encoded(identity))[:24]
    publication={'release_id':release_id,'cutoff':canonical['cutoff'],'rules_version':canonical['rules_version'],'roster':[r['name'] for r in rep['reps']],'source_versions':canonical.get('source_versions',{})}
    rep={**copy.deepcopy(rep),'publication':publication};mtd={**copy.deepcopy(mtd),'publication':publication}
    marker=f'<div role="status" style="padding:8px;text-align:center;font:12px system-ui;background:#f7f9fb">Shared reporting cutoff: {publication["cutoff"]} · Release {release_id}</div>'
    mtd_html=mtd_html.replace('<body>','<body>'+marker,1)
    mtd_html=with_shared_refresh(mtd_html,release_id,'mtd')
    stage=Path(stage);stage.mkdir(parents=True,exist_ok=True)
    contents={'rep.json':encoded(rep),'mtd.json':encoded(mtd),'mtd.html':mtd_html.encode()}
    if manager is not None:
        manager={**copy.deepcopy(manager),'publication':publication}
        manager_html=re.sub(r'(<script type="application/json" id="shared-dashboard-data">).*?(</script>)',lambda match:match[1]+encoded(manager).decode().replace('<','\\u003c')+match[2],manager_html,flags=re.S)
        manager_html=manager_html.replace('<body>','<body>'+marker,1)
        manager_html=with_shared_refresh(manager_html,release_id,'manager')
        contents.update({'manager.json':encoded(manager),'manager.html':manager_html.encode()})
    for name,content in contents.items():(stage/name).write_bytes(content)
    manifest={'schema_version':2 if manager is not None else 1,**publication,'files':{name:digest(content) for name,content in contents.items()},'parity':parity}
    (stage/'manifest.json').write_bytes(encoded(manifest));return manifest


def verify_release(directory):
    directory=Path(directory);m=json.loads((directory/'manifest.json').read_text())
    files=(*FILES,*MANAGER_FILES) if m['schema_version']==2 else FILES
    if not re.fullmatch('[a-f0-9]{24}',m['release_id']) or set(m['files'])!=set(files):raise ValueError('Invalid release manifest')
    for name in files:
        if (directory/name).is_symlink() or digest((directory/name).read_bytes())!=m['files'][name]:raise ValueError('Release hash mismatch: '+name)
    rep=json.loads((directory/'rep.json').read_text());mtd=json.loads((directory/'mtd.json').read_text());check_public(rep);check_public(mtd)
    payloads=[rep,mtd]
    if m['schema_version']==2:
        manager=json.loads((directory/'manager.json').read_text());check_public(manager);check_manager_html((directory/'manager.html').read_text(),manager)
        payloads.append(manager)
        for key in ['total_booked','total_shown','total_qualified','total_deals','total_revenue','team_qa_score','team_qa_score_count','team_post_call_12h']:
            if manager.get(key)!=rep.get(key):raise ValueError('Published manager mismatch: '+key)
        if [r['name'] for r in manager['reps']]!=[r['name'] for r in rep['reps']]:raise ValueError('Published manager roster mismatch')
    for data in payloads:
        if data.get('publication',{}).get('release_id')!=m['release_id'] or data['publication']['cutoff']!=m['cutoff']:raise ValueError('Release identity mismatch')
    check_mtd_html((directory/'mtd.html').read_text(),mtd)
    totals=mtd['totals']
    for left,right in [('total_booked','occurred_calls'),('total_shown','showed'),('total_deals','closed_won'),('total_revenue','revenue')]:
        if rep[left]!=totals[right]:raise ValueError('Published pair mismatch')
    return m


@contextmanager
def publication_lock(site):
    key=digest(str(Path(site).resolve()).encode())
    with open(Path(tempfile.gettempdir())/('shared-sales-'+key+'.lock'),'a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def pointer_for(m):return {k:m[k] for k in ['schema_version','release_id','cutoff','rules_version','files']}

def write_pointer(site,manifest):
    fd,name=tempfile.mkstemp(prefix='.accepted-',dir=site)
    try:
        with os.fdopen(fd,'wb') as target:target.write(encoded(pointer_for(manifest)));target.flush();os.fsync(target.fileno())
        os.replace(name,Path(site)/'current.json')
    finally:
        if os.path.exists(name):os.unlink(name)


def accept_release(stage,site):
    m=verify_release(stage);site=Path(site);site.mkdir(parents=True,exist_ok=True)
    with publication_lock(site):
        current=site/'current.json'
        if current.exists():
            previous=json.loads(current.read_text())
            if previous['release_id']==m['release_id']:
                if verify_release(site/'releases'/m['release_id'])!=m:raise ValueError('Accepted immutable release differs')
                return m
            if datetime.fromisoformat(previous['cutoff'])>=datetime.fromisoformat(m['cutoff']):raise ValueError('Stale or conflicting reporting cutoff')
        target=site/'releases'/m['release_id'];target.parent.mkdir(exist_ok=True)
        if target.exists():
            if verify_release(target)!=m:raise ValueError('Immutable release already differs')
        else:
            temporary=Path(tempfile.mkdtemp(prefix='.staging-',dir=target.parent))
            try:
                for name in [*m['files'],'manifest.json']:shutil.copyfile(Path(stage)/name,temporary/name)
                verify_release(temporary);os.replace(temporary,target)
            finally:
                if temporary.exists():shutil.rmtree(temporary)
        write_pointer(site,m)
    return m


def rollback_release(site,release_id):
    if not re.fullmatch('[a-f0-9]{24}',release_id):raise ValueError('Invalid rollback release ID')
    site=Path(site)
    with publication_lock(site):
        m=verify_release(site/'releases'/release_id);write_pointer(site,m)
    return m
