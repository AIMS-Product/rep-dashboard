"""Exercise the real shared-page polling script without browser or network access."""
import json,re,shutil,subprocess,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from shared_sales_release import with_shared_refresh


class SharedBrowserRefreshTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node is required for the JavaScript protocol checks')
    def test_verified_refresh_preserves_current_page_on_invalid_or_failed_reads(self):
        html=with_shared_refresh('<body></body>','a'*24,'manager')
        script=re.search(r'<script data-shared-refresh>(.*?)</script>',html,re.S).group(1)
        test=r'''
const vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const script=SCRIPT;
const bytes=Uint8Array.from(Buffer.from('<html>next report</html>'));
const hash=crypto.createHash('sha256').update(bytes).digest('hex');
async function check(overrides,expected){
 const state={calls:0,warnings:0,interval:null,redirect:null};
 const next={schema_version:2,release_id:'b'.repeat(24),files:{'manager.html':hash},...overrides};
 const context={URL,Uint8Array,crypto:crypto.webcrypto,
  location:{pathname:'/rep-dashboard/shared/releases/'+ 'a'.repeat(24) +'/manager.html',href:'https://example.test/rep-dashboard/shared/releases/'+ 'a'.repeat(24) +'/manager.html',replace:v=>state.redirect=v},
  console:{warn:()=>state.warnings++},setInterval:(fn,ms)=>{state.callback=fn;state.interval=ms},
  fetch:async()=>{state.calls++;return state.calls===1?{ok:!overrides.failed,json:async()=>next}:{ok:true,arrayBuffer:async()=>bytes.buffer}}};
 vm.runInNewContext(script,context);assert.equal(state.interval,300000);await state.callback();
 assert.equal(state.redirect,expected?'https://example.test/rep-dashboard/shared/releases/'+ 'b'.repeat(24) +'/manager.html':null);
 return state;
}
(async()=>{
 await check({},true);
 assert.equal((await check({release_id:'a'.repeat(24)},false)).calls,1);
 assert.equal((await check({files:{'manager.html':'wrong'}},false)).warnings,1);
 assert.equal((await check({schema_version:1},false)).warnings,1);
 assert.equal((await check({failed:true},false)).warnings,1);
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('SCRIPT',json.dumps(script))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'refresh.cjs';path.write_text(test)
            result=subprocess.run(['node',str(path)],text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
