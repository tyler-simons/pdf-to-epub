import test from 'node:test';
import assert from 'node:assert/strict';
import {createGateway} from '../../vercel/lib/gateway.mjs';
import {hash} from '../../vercel/lib/security.mjs';

function fixture(){
 const records=new Map(),tokens=new Set(),queue=[];const cfg={hostname:'app.test',accessSecret:'a'.repeat(32),workerSecret:'s'.repeat(32),maxBytes:40*1024*1024,maxPages:1000,jobSeconds:240,siteKey:'site',aiEnabled:false,cronSecret:'cron'};
 const store={attempt:async()=>({admitted:true}),consumeChallenge:async token=>{if(tokens.has(token))return false;tokens.add(token);return true},
  createJob:async({id,tokenHash,title,bytes,now})=>{const j={id,token_hash:tokenHash,title,expected_size:bytes,input_path:`jobs/${id}/source.pdf`,status:'uploading',created_at:now,expires_at:now+86400000};records.set(id,j);return j},
  get:async id=>records.get(id),uploadAuthorization:async({id})=>{const j=records.get(id);if(j.auth)return false;j.auth=true;return true},
  finalizeUpload:async({id})=>{records.get(id).status='queued';return true},markDeleted:async id=>{records.get(id).status='deleted'},cleanup:async()=>[],
  claim:async({id})=>({claimed:true,claimToken:'claim',job:{...records.get(id),ai_enabled:false,ai_reserved:0}}),finish:async value=>{Object.assign(records.get(value.id),value,{status:value.status});}};
 const deleted=[];const blob={head:async path=>{const j=[...records.values()].find(x=>x.input_path===path);return {pathname:path,size:j.expected_size,contentType:'application/pdf'}},
  del:async p=>deleted.push(p),setTtl:async()=>[{}],get:async()=>({statusCode:200,stream:new ReadableStream({start(c){c.enqueue(new Uint8Array(5*1024*1024));c.close()}})})};
 const handler=createGateway({cfg,store,blob,clock:()=>1700000000000,challenge:async token=>token==='valid',send:async(...args)=>queue.push(args),
  uploadHandler:async options=>{const p=options.body.payload;return {type:'blob.generate-client-token',settings:await options.onBeforeGenerateToken(p.pathname,p.clientPayload,false)}}});
 const request=(path,method='GET',payload=null,headers={})=>new Request('https://app.test'+path,{method,headers:payload?{'Content-Type':'application/json',...headers}:headers,...(payload?{body:JSON.stringify(payload)}:{})});
 return {handler,request,records,store,queue,deleted,cfg};
}
async function create(f){const r=await f.handler(f.request('/api/jobs','POST',{filename:'Book.pdf',size:50,title:'A book'},{'X-Challenge-Token':'valid'}));assert.equal(r.status,202);return r.json()}

test('public access requires verified challenge and never returns service secrets',async()=>{const f=fixture();assert.equal((await f.handler(f.request('/api/jobs','POST',{filename:'x.pdf',size:20}))).status,403);
 const cfg=await (await f.handler(f.request('/api/config'))).json();assert.equal(cfg.upload_mode,'direct');assert.equal(cfg.workerSecret,undefined);
 await create(f);assert.equal((await f.handler(f.request('/api/jobs','POST',{filename:'x.pdf',size:20},{'X-Challenge-Token':'valid'}))).status,403);
});
test('direct upload is private, exact-path, size bounded and authenticated',async()=>{const f=fixture(),job=await create(f),headers={Authorization:'Bearer '+job.token};
 assert.equal((await f.handler(f.request('/api/jobs/'+job.id))).status,404);
 const upload={type:'blob.generate-client-token',payload:{pathname:job.input_path,clientPayload:JSON.stringify({id:job.id})}};
 const r=await f.handler(f.request('/api/blob-upload','POST',upload,headers));assert.equal(r.status,200);const v=await r.json();assert.equal(v.settings.maximumSizeInBytes,50);assert.equal(v.settings.allowOverwrite,false);assert.equal(v.settings.ttlDays,1);
 const bad=await f.handler(f.request(`/api/jobs/${job.id}/finalize`,'POST',{pathname:'https://evil.test/private'},headers));assert.equal(bad.status,400);
 assert.equal((await f.handler(f.request(`/api/jobs/${job.id}/finalize`,'POST',{pathname:job.input_path},headers))).status,200);assert.equal(f.queue[0][0],'leaf-convert');assert.equal(f.queue[0][1].job_id,job.id);
});
test('large EPUB response streams and private job can be deleted',async()=>{const f=fixture(),job=await create(f),headers={Authorization:'Bearer '+job.token};f.records.get(job.id).status='ready';
 const r=await f.handler(f.request(`/api/jobs/${job.id}/download`,'GET',null,headers));assert.equal(r.status,200);assert.match(r.headers.get('cache-control'),/no-store/);assert.equal((await r.arrayBuffer()).byteLength,5*1024*1024);
 assert.equal((await f.handler(f.request(`/api/jobs/${job.id}`,'DELETE',null,headers))).status,200);
 assert.equal((await f.handler(f.request(`/api/jobs/${job.id}`,'GET',null,headers))).status,410);
});
test('worker endpoints require shared secret and expose aligned claim contract',async()=>{const f=fixture(),job=await create(f);assert.equal((await f.handler(f.request('/api/internal/claim','POST',{job_id:job.id}))).status,404);
 const r=await f.handler(f.request('/api/internal/claim','POST',{job_id:job.id},{Authorization:'Bearer '+f.cfg.workerSecret}));const c=await r.json();assert.equal(c.claim_token,'claim');assert.equal(c.job.max_seconds,240);assert.equal(c.job.ai_allowance_microusd,0);assert.equal(c.job.expires_at,1700086400);
});
test('rate limit and cross-site requests are rejected',async()=>{const f=fixture();f.store.attempt=async()=>({admitted:false});assert.equal((await f.handler(f.request('/api/jobs','POST',{filename:'Book.pdf',size:50},{'X-Challenge-Token':'valid'}))).status,429);
 assert.equal((await f.handler(f.request('/api/jobs','POST',{filename:'Book.pdf',size:50},{Origin:'https://evil.test'}))).status,403);
});
