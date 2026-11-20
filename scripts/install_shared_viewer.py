"""Install a consumer of the centrally accepted, verified dashboard release."""
import argparse,json
from pathlib import Path

HTML='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DASHBOARD_TITLE</title><style>html,body{margin:0;height:100%;font-family:system-ui;background:#f7f9fb}iframe{width:100%;height:100%;border:0}#status{padding:24px}</style></head><body><div id="status" role="status">Loading the shared reporting snapshot…</div><iframe id="dashboard" title="DASHBOARD_TITLE" hidden></iframe><script>
(async()=>{
 const root=PUBLICATION_ROOT, file=FILE_NAME;
 try {
  const response=await fetch(root+'current.json',{cache:'no-store'});
  if(!response.ok)throw new Error('Shared reporting snapshot is unavailable');
  const release=await response.json();
  if(release.schema_version!==2||!/^[a-f0-9]{24}$/.test(release.release_id)||!release.files?.[file])throw new Error('Invalid three-dashboard release');
  const htmlResponse=await fetch(root+'releases/'+release.release_id+'/'+file);
  if(!htmlResponse.ok)throw new Error('Shared dashboard is unavailable');
  const bytes=await htmlResponse.arrayBuffer();
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),value=>value.toString(16).padStart(2,'0')).join('');
  if(hash!==release.files[file])throw new Error('Shared dashboard verification failed');
  const frame=document.getElementById('dashboard');
  frame.addEventListener('load',()=>{document.getElementById('status').hidden=true;frame.hidden=false;},{once:true});
  window.location.replace(root+'releases/'+release.release_id+'/'+file);
 }catch(error){document.getElementById('status').textContent='Could not load dashboard: '+error.message;}
})();
</script></body></html>'''


def install(output,publication_root,surface='mtd'):
    if surface not in {'mtd','manager'}:raise ValueError('Unknown dashboard surface')
    root=publication_root.rstrip('/')+'/'
    title='MTD Funnel Performance' if surface=='mtd' else 'Sales Manager Dashboard'
    text=HTML.replace('DASHBOARD_TITLE',title).replace('PUBLICATION_ROOT',json.dumps(root).replace('<','\\u003c')).replace('FILE_NAME',json.dumps(surface+'.html'))
    Path(output).write_text(text)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path(__file__).with_name('index.html'));p.add_argument('--surface',choices=['mtd','manager'],default='mtd');p.add_argument('--publication-root',default='https://aims-product.github.io/rep-dashboard/shared/');a=p.parse_args();install(a.output,a.publication_root,a.surface)
