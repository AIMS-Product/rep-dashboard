"""Run the shared publication boundary against real local Git repositories."""
import os,shutil,subprocess,tempfile,unittest
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[1]/'scripts/refresh_shared_sales.sh'
FIXTURE='''import argparse,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('--mtd-root');p.add_argument('--manager-root');p.add_argument('--manager-base');p.add_argument('--output');p.add_argument('--stage');p.add_argument('--site-root');a=p.parse_args()
if a.command=='build':
 if os.environ.get('FAIL_BUILD'):raise RuntimeError('fixture failure')
 out=Path(a.output);out.mkdir(parents=True);(out/'rep.json').write_text(Path('input.txt').read_text())
 if os.environ.get('RACE'):
  other=Path(os.environ['COMPETITOR']);(other/'input.txt').write_text('new source');(other/'data.json').write_text('other accepted data')
  for cmd in [('add','.'),('commit','-m','concurrent source change'),('push','origin','main')]:subprocess.run(['git','-C',str(other),*cmd],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
else:
 root=Path(a.site_root);root.mkdir(exist_ok=True);(root/'current.json').write_text((Path(a.stage)/'rep.json').read_text())
'''

class SharedPublishScriptTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.remote=self.root/'remote.git';self.repo=self.root/'repo';self.other=self.root/'other'
  self.env={**os.environ,'GITHUB_ACTIONS':'true','GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_SYSTEM':os.devnull,'COMPETITOR':str(self.other)}
  self.git(self.root,'init','--bare','--initial-branch=main',str(self.remote));self.git(self.root,'clone',str(self.remote),str(self.repo))
  self.git(self.repo,'config','user.name','Fixture');self.git(self.repo,'config','user.email','fixture@test')
  (self.repo/'scripts').mkdir();(self.repo/'archives').mkdir();shutil.copy(SCRIPT,self.repo/'scripts/refresh_shared_sales.sh');shutil.copy(SCRIPT.with_name('refresh_and_publish.sh'),self.repo/'scripts/refresh_and_publish.sh')
  (self.repo/'scripts/publish_sales_dashboards.py').write_text(FIXTURE);(self.repo/'scripts/build_yesterday_preview.py').write_text('')
  (self.repo/'input.txt').write_text('one accepted source');(self.repo/'data.json').write_text('old data');(self.repo/'daily-preview.html').write_text('archive')
  self.git(self.repo,'add','.');self.git(self.repo,'commit','-m','fixture');self.git(self.repo,'push','origin','main');self.git(self.root,'clone',str(self.remote),str(self.other))
  self.git(self.other,'config','user.name','Fixture');self.git(self.other,'config','user.email','fixture@test')
 def git(self,repo,*args):return subprocess.check_output(['git','-C',str(repo),*args],env=self.env,text=True,stderr=subprocess.STDOUT).strip()
 def run_publish(self,**env):return subprocess.run(['bash','scripts/refresh_shared_sales.sh'],cwd=self.repo,env={**self.env,**env},capture_output=True,text=True)
 def test_both_outputs_are_pushed_in_one_commit(self):
  result=self.run_publish();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
  self.assertEqual(self.git(self.remote,'show','main:data.json'),'one accepted source');self.assertEqual(self.git(self.remote,'show','main:shared/current.json'),'one accepted source')
  self.assertEqual(self.git(self.remote,'rev-list','--count','main'),'2')
 def test_source_race_does_not_publish_stale_pair(self):
  result=self.run_publish(RACE='1');self.assertNotEqual(result.returncode,0);self.assertIn('Main changed',result.stderr)
  self.assertEqual(self.git(self.remote,'show','main:data.json'),'other accepted data')
  self.assertNotIn('shared/current.json',self.git(self.remote,'ls-tree','-r','--name-only','main'))
 def test_generation_failure_does_not_push(self):
  before=self.git(self.remote,'rev-parse','main');result=self.run_publish(FAIL_BUILD='1');self.assertNotEqual(result.returncode,0)
  self.assertEqual(self.git(self.remote,'rev-parse','main'),before)

 def test_old_entrypoint_delegates_to_shared_publisher(self):
  result=subprocess.run(['bash','scripts/refresh_and_publish.sh'],cwd=self.repo,env=self.env,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
  self.assertEqual(self.git(self.remote,'show','main:shared/current.json'),'one accepted source')
