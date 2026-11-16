import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { execFileSync } from 'node:child_process';
import { createInterface } from 'node:readline';
import { Store, StoreError } from '../../vercel/lib/store.mjs';

const PYTHON = process.env.LEAF_TEST_PYTHON || 'python3';
const BRIDGE = `import sys,json,fakeredis
server=fakeredis.FakeServer()
r=fakeredis.FakeRedis(server=server,decode_responses=True)
for line in sys.stdin:
 try:
  q=json.loads(line)
  if q['op']=='eval':
   value=r.eval(q['script'],len(q['keys']),*q['keys'],*q['args'])
  elif q['op']=='hgetall': value=r.hgetall(q['key'])
  elif q['op']=='get': value=r.get(q['key'])
  elif q['op']=='ttl': value=r.ttl(q['key'])
  else: raise ValueError('unknown operation')
  print(json.dumps({'ok':True,'value':value}),flush=True)
 except Exception as e:
  print(json.dumps({'ok':False,'error':str(e)}),flush=True)
`;
let hasLua = false;
try { execFileSync(PYTHON, ['-c', 'import fakeredis,lupa']); hasLua = true; } catch { /* optional local Lua test runtime */ }
if (process.env.CI && !hasLua) throw new Error('CI requires fakeredis[lua]; set LEAF_TEST_PYTHON if needed');

class LuaRedis {
  constructor() {
    this.child = spawn(PYTHON, ['-u', '-c', BRIDGE], { stdio: ['pipe', 'pipe', 'pipe'] });
    this.lines = createInterface({ input: this.child.stdout });
    this.pending = [];
    this.lines.on('line', line => {
      const item = this.pending.shift();
      if (!item) return;
      const result = JSON.parse(line);
      if (result.ok) item.resolve(result.value); else item.reject(new Error(result.error));
    });
    this.child.stderr.resume();
  }
  request(value) {
    return new Promise((resolve, reject) => {
      this.pending.push({ resolve, reject });
      this.child.stdin.write(JSON.stringify(value) + '\n');
    });
  }
  eval(script, keys, args) { return this.request({ op: 'eval', script, keys, args }); }
  hgetall(key) { return this.request({ op: 'hgetall', key }); }
  get(key) { return this.request({ op: 'get', key }); }
  async close() { this.child.stdin.end(); await new Promise(resolve => this.child.once('exit', resolve)); }
}

const withStore = async (options, fn) => {
  const redis = new LuaRedis();
  try { await fn(new Store(redis, options), redis); } finally { await redis.close(); }
};
const owner = 'a'.repeat(64);
const tokenHash = id => `token-hash-${id}`;
const jobInput = (id, now, options = {}) => ({ id, tokenHash: tokenHash(id), ownerHash: options.ownerHash || owner,
  title: `Book ${id}`, bytes: 1000, now });
const upload = async (store, job, now) => {
  assert.equal(await store.uploadAuthorization({ id: job.id, tokenHash: tokenHash(job.id), now }), true);
  assert.equal(await store.finalizeUpload({ id: job.id, inputPath: job.input_path, size: job.expected_size, now }), true);
};

test('rolling attempts and challenge replay prevention run through real Lua', { skip: !hasLua }, async () => {
  await withStore({ attemptsPerHour: 2 }, async (store) => {
    const first = await store.attempt(owner, 1_800_000_000_000);
    const second = await store.attempt(owner, 1_800_000_000_001);
    const blocked = await store.attempt(owner, 1_800_000_000_002);
    assert.equal(first.admitted, true);
    assert.equal(second.admitted, true);
    assert.equal(blocked.admitted, false);
    assert.equal(blocked.count, 2);
    assert.equal(await store.consumeChallenge('sha256-token-hash'), true);
    assert.equal(await store.consumeChallenge('sha256-token-hash'), false);
  });
});

test('atomic create admission caps pending, daily jobs, compute, and client jobs', { skip: !hasLua }, async () => {
  await withStore({ dailyJobs: 20, perClientJobs: 20, dailySeconds: 1000,
    maxPending: 3, jobSeconds: 100, aiEnabled: false }, async (store) => {
    const now = 1_800_000_000_000;
    const results = await Promise.allSettled(Array.from({ length: 10 }, (_, i) =>
      store.createJob(jobInput(`q${i}`, now))));
    assert.equal(results.filter(r => r.status === 'fulfilled').length, 3);
    assert.ok(results.filter(r => r.status === 'rejected').every(r => r.reason instanceof StoreError));
    const record = await store.get('q0');
    assert.equal(record.status, 'uploading');
    assert.equal(record.expected_size, 1000);
    assert.equal(record.max_seconds, 100);
    assert.equal(record.ai_enabled, false);
  });
});

test('upload token is single-use, path/size checked, and claim is atomic', { skip: !hasLua }, async () => {
  await withStore({ aiEnabled: true, aiCap: 300_000, aiPerJob: 100_000 }, async (store, redis) => {
    const now = 1_800_000_000_000;
    const job = await store.createJob(jobInput('upload', now));
    assert.equal(job.ai_reserved, 100_000);
    assert.equal(await store.uploadAuthorization({ id: 'upload', tokenHash: 'wrong', now }), false);
    assert.equal(await store.uploadAuthorization({ id: 'upload', tokenHash: tokenHash('upload'), now }), true);
    assert.equal(await store.uploadAuthorization({ id: 'upload', tokenHash: tokenHash('upload'), now }), false);
    assert.equal(await store.finalizeUpload({ id: 'upload', inputPath: 'jobs/other/source.pdf', size: 1000, now }), false);
    assert.equal(await store.finalizeUpload({ id: 'upload', inputPath: job.input_path, size: 999, now }), false);
    assert.equal(await store.finalizeUpload({ id: 'upload', inputPath: job.input_path, size: 1000, now }), true);
    assert.equal(await store.finalizeUpload({ id: 'upload', inputPath: job.input_path, size: 1000, now }), false);
    const claims = await Promise.all([store.claim({ id: 'upload', now }), store.claim({ id: 'upload', now })]);
    assert.equal(claims.filter(c => c.claimed).length, 1);
    const claimed = claims.find(c => c.claimed);
    assert.equal(claimed.job.ai_enabled, true);
    assert.equal(claimed.job.ai_allowance_microusd, 100_000);
    assert.equal(claimed.job.max_pages, 1000);
    assert.equal('token_hash' in claimed.job, false);
    assert.equal('owner_hash' in claimed.job, false);
    assert.equal(await redis.get(store.key('pending')), '1');
  });
});

test('AI lifetime cap only decreases; exhausted AI leaves local job admissible', { skip: !hasLua }, async () => {
  const redis = new LuaRedis();
  try {
    const now = 1_800_000_000_000;
    const firstStore = new Store(redis, { aiEnabled: true, aiCap: 250_000, aiPerJob: 100_000,
      dailyJobs: 20, perClientJobs: 20, dailySeconds: 10000, maxPending: 20, jobSeconds: 10 });
    const first = await firstStore.createJob(jobInput('ai1', now));
    assert.equal(first.ai_reserved, 100_000);
    const lowerStore = new Store(redis, { aiEnabled: true, aiCap: 100_000, aiPerJob: 100_000,
      dailyJobs: 20, perClientJobs: 20, dailySeconds: 10000, maxPending: 20, jobSeconds: 10 });
    const second = await lowerStore.createJob(jobInput('ai2', now + 1));
    assert.equal(second.ai_reserved, 0);
    assert.equal(second.ai_enabled, false);
    const raiseStore = new Store(redis, { aiEnabled: true, aiCap: 5_000_000, aiPerJob: 100_000,
      dailyJobs: 20, perClientJobs: 20, dailySeconds: 10000, maxPending: 20, jobSeconds: 10 });
    const third = await raiseStore.createJob(jobInput('ai3', now + 2));
    assert.equal(third.ai_reserved, 0);
    assert.equal(await redis.get(raiseStore.key('ai:cap')), '100000');
  } finally { await redis.close(); }
});

test('finish settles known AI and compute exactly once, retaining unknown AI reserve', { skip: !hasLua }, async () => {
  await withStore({ aiEnabled: true, aiCap: 500_000, aiPerJob: 100_000,
    dailyJobs: 20, perClientJobs: 20, dailySeconds: 10000, maxPending: 20, jobSeconds: 100 }, async (store, redis) => {
    const now = 1_800_000_000_000;
    const job = await store.createJob(jobInput('finish', now));
    await upload(store, job, now);
    const { claimToken } = await store.claim({ id: 'finish', now });
    const done = await store.finish({ id: 'finish', claimToken, status: 'ready', seconds: 12,
      aiCharge: 1234, aiKnown: true, report: { pages: 2 }, outputPath: 'jobs/finish/book.epub', now: now + 100 });
    assert.deepEqual(done, { finished: true, duplicate: false });
    assert.deepEqual(await store.finish({ id: 'finish', claimToken, status: 'ready', seconds: 12,
      aiCharge: 1234, aiKnown: true, now: now + 101 }), { finished: true, duplicate: true });
    const record = await store.get('finish');
    assert.equal(record.status, 'ready');
    assert.deepEqual(record.report, { pages: 2 });
    assert.equal(await redis.get(store.key('ai:reserved')), '0');
    assert.equal(await redis.get(store.key('ai:spent')), '1234');
    assert.equal(await redis.get(store.key(`daily:${store.dayKey(now)}:seconds`)), '12');
    assert.equal(await redis.get(store.key('pending')), '0');
    assert.equal((await store.markDeleted('finish')).outputPath, 'jobs/finish/book.epub');
    assert.equal(await store.markDeleted('finish'), null);
  });
});

test('unknown AI settlement holds lifetime reservation; abandoned uploads refund it', { skip: !hasLua }, async () => {
  await withStore({ aiEnabled: true, aiCap: 500_000, aiPerJob: 100_000,
    dailyJobs: 20, perClientJobs: 20, dailySeconds: 1000, maxPending: 20, jobSeconds: 20,
    uploadTimeoutSeconds: 2, retentionSeconds: 100 }, async (store, redis) => {
    const now = 1_800_000_000_000;
    const unknown = await store.createJob(jobInput('unknown', now));
    await upload(store, unknown, now);
    const claim = await store.claim({ id: 'unknown', now });
    await store.finish({ id: 'unknown', claimToken: claim.claimToken, status: 'failed', seconds: 4, now: now + 5 });
    assert.equal(await redis.get(store.key('ai:reserved')), '100000');
    const unfinished = await store.createJob(jobInput('upload-abandon', now + 10));
    const cleanup = await store.cleanup({ now: now + 2011, limit: 10 });
    assert.ok(cleanup.some(x => x.id === unfinished.id && x.action === 'upload_abandoned'));
    assert.equal(await redis.get(store.key('ai:reserved')), '100000');
    assert.equal((await store.get('upload-abandon')).status, 'failed');
  });
});

test('deleting an unclaimed uploading job releases pending, AI, and compute reservations', { skip: !hasLua }, async () => {
  await withStore({ aiEnabled: true, aiCap: 300_000, aiPerJob: 100_000,
    dailyJobs: 20, perClientJobs: 20, dailySeconds: 1000, maxPending: 20, jobSeconds: 20 }, async (store, redis) => {
    const now = 1_800_000_000_000;
    const job = await store.createJob(jobInput('abort-upload', now));
    const deleted = await store.markDeleted(job.id);
    assert.equal(deleted.inputPath, 'jobs/abort-upload/source.pdf');
    assert.equal((await store.get(job.id)).status, 'deleted');
    assert.equal(await redis.get(store.key('pending')), '0');
    assert.equal(await redis.get(store.key('ai:reserved')), '0');
    assert.equal(await redis.get(store.key(`daily:${store.dayKey(now)}:seconds`)), '0');
  });
});


test('empty cleanup returns an array', {skip:!hasLua}, async()=>{
 await withStore({},async store=>{assert.deepEqual(await store.cleanup(),[])});
});


test('pending metadata survives outages until cleanup releases reservations', {skip:!hasLua},async()=>{
 await withStore({},async(store,redis)=>{
  const now=1800000000000;const job=await store.createJob(jobInput('outage',now));
  assert.equal(await redis.request({op:'ttl',key:store.key('job:'+job.id)}),-1);
  await store.cleanup({now:now+86400001});
  assert.ok(await redis.request({op:'ttl',key:store.key('job:'+job.id)})>0);
 });
});
