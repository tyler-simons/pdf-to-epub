import { randomUUID } from 'node:crypto';

const SCRIPTS = {
  attempt: `-- STORE:attempt
local now=tonumber(ARGV[1]); local limit=tonumber(ARGV[2]); local member=ARGV[3]
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-3600000)
local count=redis.call('ZCARD', KEYS[1])
local first=redis.call('ZRANGE',KEYS[1],0,0,'WITHSCORES'); local reset=now+3600000
if #first>1 then reset=tonumber(first[2])+3600000 end
if count >= limit then return {0,count,reset} end
redis.call('ZADD', KEYS[1], now, member)
redis.call('EXPIRE', KEYS[1], 172800)
return {1,count+1,reset}`,
  createJob: `-- STORE:createJob
local now=tonumber(ARGV[1]); local day=ARGV[2]; local client=ARGV[3]
local dailyLimit=tonumber(ARGV[4]); local clientLimit=tonumber(ARGV[5]); local secondsLimit=tonumber(ARGV[6])
local pendingLimit=tonumber(ARGV[7]); local jobSeconds=tonumber(ARGV[8]); local aiWanted=tonumber(ARGV[9])
local aiPerJob=tonumber(ARGV[10]); local configCap=tonumber(ARGV[11]); local retention=tonumber(ARGV[12])
if redis.call('EXISTS',KEYS[1])==1 then return {0,'id_conflict'} end
local existing=redis.call('GET',KEYS[9]); local cap=configCap
if existing then
 cap=math.min(tonumber(existing),configCap)
 if cap<tonumber(existing) then redis.call('SET',KEYS[9],cap) end
else redis.call('SET',KEYS[9],configCap) end
local jobs=tonumber(redis.call('GET',KEYS[3]) or '0')
local clientJobs=tonumber(redis.call('GET',KEYS[4]) or '0')
local seconds=tonumber(redis.call('GET',KEYS[5]) or '0')
local pending=tonumber(redis.call('GET',KEYS[2]) or '0')
local reserve=0; if aiWanted==1 then reserve=aiPerJob end
local used=tonumber(redis.call('GET',KEYS[7]) or '0')+tonumber(redis.call('GET',KEYS[8]) or '0')
if jobs>=dailyLimit then return {0,'daily_limit'} end
if clientJobs>=clientLimit then return {0,'client_limit'} end
if seconds+jobSeconds>secondsLimit then return {0,'compute_limit'} end
if pending>=pendingLimit then return {0,'queue_full'} end
if used+reserve>cap then reserve=0 end
local id=ARGV[13]; local tokenHash=ARGV[14]; local ownerHash=ARGV[15]; local title=ARGV[16]
local inputPath=ARGV[18]; local expected=tonumber(ARGV[19]); local jobKey=KEYS[1]
local expires=now+retention*1000; local uploadUntil=now+tonumber(ARGV[20])*1000
redis.call('HSET',jobKey,'id',id,'token_hash',tokenHash,'owner_hash',ownerHash,'title',title,
 'status','uploading','stage','Uploading','created_at',now,'expires_at',expires,'upload_until',uploadUntil,
 'expected_size',expected,'input_path',inputPath,'output_path','','reservation_seconds',jobSeconds,'day_key',day,
 'actual_seconds','','ai_reserved',reserve,'ai_charge','','ai_known','0','pending_active','1',
 'claim_token','','claim_deadline','','error','','report','','preview','',
 'ai_enabled',(aiWanted==1 and reserve>0) and '1' or '0','max_pages',ARGV[21])
redis.call('EXPIRE',jobKey,retention+604800)
redis.call('ZADD',KEYS[10],expires,id); redis.call('ZADD',KEYS[11],uploadUntil,id)
redis.call('INCR',KEYS[2]); redis.call('INCR',KEYS[3]); redis.call('EXPIRE',KEYS[3],259200)
redis.call('INCR',KEYS[4]); redis.call('EXPIRE',KEYS[4],259200)
redis.call('INCRBY',KEYS[5],jobSeconds); redis.call('EXPIRE',KEYS[5],259200)
if reserve>0 then redis.call('INCRBY',KEYS[8],reserve) end
return {1,id}`,
  authorizeUpload: `-- STORE:authorizeUpload
local job=redis.call('HGETALL',KEYS[1]); if #job==0 then return 0 end
local t={}; for i=1,#job,2 do t[job[i]]=job[i+1] end
if t.status~='uploading' or t.token_hash~=ARGV[1] or tonumber(ARGV[2])>tonumber(t.upload_until) or t.upload_authorized=='1' then return 0 end
redis.call('HSET',KEYS[1],'upload_authorized','1'); return 1`,
  finalizeUpload: `-- STORE:finalizeUpload
local job=redis.call('HGETALL',KEYS[1]); if #job==0 then return 0 end
local t={}; for i=1,#job,2 do t[job[i]]=job[i+1] end
if t.status~='uploading' or t.upload_authorized~='1' or t.input_path~=ARGV[1]
 or tonumber(t.expected_size)~=tonumber(ARGV[2]) or tonumber(ARGV[2])<=0
 or tonumber(ARGV[2])>tonumber(ARGV[3]) or tonumber(ARGV[4])>tonumber(t.upload_until) then return 0 end
redis.call('HSET',KEYS[1],'status','queued','stage','Waiting for a free worker','expires_at',ARGV[5])
redis.call('ZADD',KEYS[2],ARGV[5],t.id); redis.call('ZREM',KEYS[3],t.id)
return 1`,
  claim: `-- STORE:claim
local job=redis.call('HGETALL',KEYS[1]); if #job==0 then return {0,'missing'} end
local t={}; for i=1,#job,2 do t[job[i]]=job[i+1] end
local now=tonumber(ARGV[1]); local lease=tonumber(ARGV[2])
if tonumber(t.expires_at)<=now then return {0,'expired'} end
if t.status=='processing' and tonumber(t.claim_deadline or '0')<=now then
 redis.call('HSET',KEYS[1],'status','failed','stage','Worker lease expired','error','Processing worker stopped before completion.')
 if t.pending_active=='1' then redis.call('DECR',KEYS[2]); redis.call('HSET',KEYS[1],'pending_active','0') end
 redis.call('ZADD',KEYS[3],tonumber(t.expires_at),t.id)
 return {0,'lease_expired'}
end
if t.status~='queued' then return {0,'not_queued'} end
local claim=ARGV[3]; local deadline=now+(lease+40)*1000
redis.call('HSET',KEYS[1],'status','processing','stage','Processing','claim_token',claim,'claim_deadline',deadline,'started_at',now)
redis.call('ZADD',KEYS[3],deadline,t.id)
return {1,claim}`,
  finish: `-- STORE:finish
local job=redis.call('HGETALL',KEYS[1]); if #job==0 then return {0,'missing'} end
local t={}; for i=1,#job,2 do t[job[i]]=job[i+1] end
if t.claim_token~=ARGV[1] or t.claim_token=='' then return {0,'claim_mismatch'} end
if t.status=='ready' or t.status=='failed' or t.status=='expired' or t.status=='deleted' then return {1,'duplicate'} end
if t.status~='processing' then return {0,'not_processing'} end
local status=ARGV[2]; local now=tonumber(ARGV[3]); local seconds=tonumber(ARGV[4]); local aiCharge=tonumber(ARGV[5]); local known=ARGV[6]=='1'
if status~='ready' and status~='failed' then return {0,'bad_status'} end
if status=='ready' and ARGV[11]~='jobs/'..t.id..'/book.epub' then return {0,'output_path'} end
local reserved=tonumber(t.ai_reserved or '0'); local reservedSeconds=tonumber(t.reservation_seconds or '0')
if seconds<0 or seconds>reservedSeconds then return {0,'bad_seconds'} end
if known and (aiCharge<0 or aiCharge>reserved) then return {0,'bad_ai_charge'} end
if known and reserved>0 then redis.call('DECRBY',KEYS[3],reserved); if aiCharge>0 then redis.call('INCRBY',KEYS[4],aiCharge) end end
if seconds<reservedSeconds then redis.call('DECRBY',ARGV[12]..':daily:'..t.day_key..':seconds',reservedSeconds-seconds) end
redis.call('HSET',KEYS[1],'status',status,'stage',ARGV[7],'actual_seconds',seconds,
 'ai_charge',known and aiCharge or '','ai_known',known and '1' or '0','error',ARGV[8],
 'report',ARGV[9],'preview',ARGV[10],'output_path',ARGV[11],'finished_at',now,'pending_active','0')
if t.pending_active=='1' then redis.call('DECR',KEYS[2]) end
redis.call('ZADD',KEYS[6],tonumber(t.expires_at),t.id)
return {1,'finished'}`,
  cleanup: `-- STORE:cleanup
local now=tonumber(ARGV[1]); local limit=tonumber(ARGV[2]); local retention=tonumber(ARGV[3])
local result={}; local uploads=redis.call('ZRANGEBYSCORE',KEYS[2],'-inf',now,'LIMIT',0,limit)
for _,id in ipairs(uploads) do
 local key=ARGV[4]..':job:'..id; local raw=redis.call('HGETALL',key); redis.call('ZREM',KEYS[2],id)
 if #raw>0 then
  local t={}; for i=1,#raw,2 do t[raw[i]]=raw[i+1] end
  if t.status=='uploading' and tonumber(t.upload_until)<=now then
   redis.call('ZREM',KEYS[1],id)
   if t.pending_active=='1' then redis.call('DECR',KEYS[3]) end
   local seconds=tonumber(t.reservation_seconds or '0'); if seconds>0 then redis.call('DECRBY',ARGV[4]..':daily:'..t.day_key..':seconds',seconds) end
   local reserve=tonumber(t.ai_reserved or '0'); if reserve>0 then redis.call('DECRBY',KEYS[4],reserve) end
   local expires=tonumber(t.expires_at); redis.call('HSET',key,'status','failed','stage','Upload abandoned','error','Upload did not finish.','pending_active','0','reservation_seconds','0','ai_reserved','0')
   redis.call('ZADD',KEYS[1],expires,id)
   table.insert(result,{id=id,input_path=t.input_path,output_path=t.output_path,action='upload_abandoned'})
  end
 end
end
local ids=redis.call('ZRANGEBYSCORE',KEYS[1],'-inf',now,'LIMIT',0,math.max(0,limit-#uploads))
for _,id in ipairs(ids) do
 local key=ARGV[4]..':job:'..id; local raw=redis.call('HGETALL',key); redis.call('ZREM',KEYS[1],id)
 if #raw>0 then
  local t={}; for i=1,#raw,2 do t[raw[i]]=raw[i+1] end
  if t.status=='processing' and tonumber(t.claim_deadline or '0')>now then
   redis.call('ZADD',KEYS[1],tonumber(t.claim_deadline),id)
  elseif t.status=='processing' and tonumber(t.claim_deadline or '0')<=now and tonumber(t.expires_at)>now then
   if t.pending_active=='1' then redis.call('DECR',KEYS[3]) end
   redis.call('HSET',key,'status','failed','stage','Worker lease expired','error','Processing worker stopped before completion.','pending_active','0')
   redis.call('ZADD',KEYS[1],tonumber(t.expires_at),id)
   table.insert(result,{id=id,input_path=t.input_path,output_path=t.output_path,action='worker_abandoned'})
  elseif tonumber(t.expires_at)<=now then
   if t.pending_active=='1' then redis.call('DECR',KEYS[3]) end
   if t.status=='queued' then
    local seconds=tonumber(t.reservation_seconds or '0'); if seconds>0 then redis.call('DECRBY',ARGV[4]..':daily:'..t.day_key..':seconds',seconds) end
    local reserve=tonumber(t.ai_reserved or '0'); if reserve>0 then redis.call('DECRBY',KEYS[4],reserve) end
   end
   redis.call('HSET',key,'status','expired','stage','Expired','pending_active','0','report','','preview','')
   redis.call('EXPIRE',key,604800)
   table.insert(result,{id=id,input_path=t.input_path,output_path=t.output_path,action='expired'})
  end
 end
end
return cjson.encode(result)`,
  markDeleted: `-- STORE:markDeleted
local raw=redis.call('HGETALL',KEYS[1]); if #raw==0 then return {0} end
local t={}; for i=1,#raw,2 do t[raw[i]]=raw[i+1] end
if t.status=='queued' or t.status=='processing' then return {0} end
if t.status=='deleted' then return {0} end
if t.status=='uploading' then
 if t.pending_active=='1' then redis.call('DECR',KEYS[2]) end
 local seconds=tonumber(t.reservation_seconds or '0'); if seconds>0 then redis.call('DECRBY',KEYS[4],seconds) end
 local reserve=tonumber(t.ai_reserved or '0'); if reserve>0 then redis.call('DECRBY',KEYS[3],reserve) end
 redis.call('ZREM',KEYS[5],t.id); redis.call('ZREM',KEYS[6],t.id)
end
redis.call('HSET',KEYS[1],'status','deleted','stage','Deleted','report','','preview','')
redis.call('HSET',KEYS[1],'pending_active','0','reservation_seconds','0','ai_reserved','0')
redis.call('EXPIRE',KEYS[1],604800)
return {1,t.id,t.input_path or '',t.output_path or ''}`,
  consumeChallenge: `-- STORE:consumeChallenge
local result=redis.call('SET',KEYS[1],'1','NX','EX',600); if result then return 1 else return 0 end`,
};

export class StoreError extends Error {
  constructor(code, message = code) { super(message); this.name = 'StoreError'; this.code = code; }
}

/** Shared Upstash Redis store. All admission and state transitions use EVAL. */
export class Store {
  constructor(redis, {
    namespace = 'leaf:{v1}', aiCap = 9_000_000, dailyJobs = 20,
    perClientJobs = 3, dailySeconds = 3600, maxPending = 10,
    jobSeconds = 240, retentionSeconds = 86400, attemptsPerHour = 12,
    aiEnabled = false, aiPerJob = 100_000, maxUploadBytes = 40 * 1024 * 1024,
    uploadTimeoutSeconds = 300, maxPages = 1000,
  } = {}) {
    if (!redis?.eval) throw new TypeError('redis must implement eval(script, keys, args)');
    this.redis = redis;
    const cleanedNamespace = namespace.replace(/:+$/, '');
    this.namespace = cleanedNamespace.includes('{') ? cleanedNamespace : `${cleanedNamespace}:{leaf}`;
    Object.assign(this, { aiCap, dailyJobs, perClientJobs, dailySeconds, maxPending,
      jobSeconds, retentionSeconds, attemptsPerHour, aiEnabled, aiPerJob,
      maxUploadBytes, uploadTimeoutSeconds, maxPages });
    for (const [name, value] of Object.entries({aiCap,dailyJobs,perClientJobs,dailySeconds,maxPending,
      jobSeconds,retentionSeconds,attemptsPerHour,aiPerJob,maxUploadBytes,uploadTimeoutSeconds,maxPages})) {
      if (!Number.isSafeInteger(value) || value < 0) throw new RangeError(`${name} must be a nonnegative safe integer`);
    }
  }

  key(suffix) { return `${this.namespace}:${suffix}`; }
  dayKey(now) { return new Date(now).toISOString().slice(0, 10); }
  async run(name, keys, args) {
    return this.redis.eval(SCRIPTS[name], keys.map(String), args.map(v => String(v ?? '')));
  }

  async attempt(ownerHash, now = Date.now()) {
    if (typeof ownerHash !== 'string' || !ownerHash) throw new TypeError('ownerHash is required');
    const result = await this.run('attempt', [this.key(`attempts:${encodeURIComponent(ownerHash)}`)],
      [now, this.attemptsPerHour, `${now}:${randomUUID()}`]);
    return { admitted: Number(result[0]) === 1, count: Number(result[1]), resetAt: Number(result[2]) };
  }

  async createJob({ id = randomUUID().replaceAll('-', ''), tokenHash, ownerHash, title = 'Book', bytes,
    now = Date.now() } = {}) {
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(id)) throw new TypeError('id is invalid');
    if (typeof tokenHash !== 'string' || !tokenHash || typeof ownerHash !== 'string' || !ownerHash) throw new TypeError('tokenHash and ownerHash are required');
    if (!Number.isSafeInteger(bytes) || bytes <= 0 || bytes > this.maxUploadBytes) throw new StoreError('upload_size');
    const day = this.dayKey(now);
    const keys = [this.key(`job:${id}`), this.key('pending'), this.key(`daily:${day}:jobs`),
      this.key(`daily:${day}:client:${encodeURIComponent(ownerHash)}:jobs`), this.key(`daily:${day}:seconds`),
      this.key(`unused:${day}`), this.key('ai:spent'), this.key('ai:reserved'), this.key('ai:cap'),
      this.key('expiry'), this.key('upload-deadlines')];
    const inputPath = `jobs/${id}/source.pdf`;
    const result = await this.run('createJob', keys, [now, day, ownerHash, this.dailyJobs,
      this.perClientJobs, this.dailySeconds, this.maxPending, this.jobSeconds,
      this.aiEnabled ? 1 : 0, this.aiPerJob, this.aiCap, this.retentionSeconds,
      id, tokenHash, ownerHash, String(title).slice(0, 160), bytes, inputPath, bytes,
      this.uploadTimeoutSeconds, this.maxPages]);
    if (Number(result[0]) !== 1) throw new StoreError(result[1]);
    // The trusted gateway needs the stored hash for its one-shot upload grant;
    // it returns only the raw bearer token, never this digest, to the browser.
    return this.get(id);
  }

  async uploadAuthorization({ id, tokenHash, now = Date.now() }) {
    return Number(await this.run('authorizeUpload', [this.key(`job:${id}`)], [tokenHash, now])) === 1;
  }

  async finalizeUpload({ id, inputPath, size, now = Date.now() }) {
    if (!Number.isSafeInteger(size) || size <= 0 || size > this.maxUploadBytes) return false;
    const expiresAt = now + this.retentionSeconds * 1000;
    const result = await this.run('finalizeUpload', [this.key(`job:${id}`), this.key('expiry'), this.key('upload-deadlines')],
      [inputPath, size, this.maxUploadBytes, now, expiresAt]);
    return Number(result) === 1;
  }

  async claim({ id, now = Date.now() }) {
    const claimToken = randomUUID();
    const result = await this.run('claim', [this.key(`job:${id}`), this.key('pending'), this.key('expiry')],
      [now, this.jobSeconds, claimToken]);
    if (Number(result[0]) !== 1) return { claimed: false, reason: result[1] };
    const job = this.safeJob(await this.get(id));
    return { claimed: true, claimToken, job };
  }

  safeJob(job) {
    if (!job) return null;
    const safe = { ...job };
    for (const field of ['token_hash','owner_hash','claim_token']) delete safe[field];
    safe.max_seconds = safe.reservation_seconds;
    safe.ai_allowance_microusd = safe.ai_reserved;
    return safe;
  }

  async finish({ id, claimToken, status, report = null, preview = null, aiCharge = 0,
    aiKnown = false, seconds, outputPath = '', error = '', now = Date.now() }) {
    if (!Number.isSafeInteger(seconds) || seconds < 0) throw new TypeError('seconds is required');
    if (!Number.isSafeInteger(aiCharge) || aiCharge < 0) throw new TypeError('aiCharge must be nonnegative');
    if (typeof claimToken !== 'string' || !claimToken) throw new TypeError('claimToken is required');
    const current = await this.get(id);
    if (!current) throw new StoreError('missing');
    const keys = [this.key(`job:${id}`), this.key('pending'), this.key('ai:reserved'),
      this.key('ai:spent'), this.key(`daily:${current.day_key}:seconds`), this.key('expiry')];
    const result = await this.run('finish', keys, [claimToken, status, now, seconds,
      aiCharge, aiKnown ? 1 : 0, status === 'ready' ? 'Ready' : 'Failed', error,
      report == null ? '' : JSON.stringify(report), preview == null ? '' : JSON.stringify(preview), outputPath,
      this.namespace]);
    if (Number(result[0]) !== 1) throw new StoreError(result[1]);
    return { finished: true, duplicate: result[1] === 'duplicate' };
  }

  async get(id) {
    const raw = await this.redis.hgetall(this.key(`job:${id}`));
    if (!raw || Object.keys(raw).length === 0) return null;
    const numberFields = ['created_at','expires_at','upload_until','expected_size','reservation_seconds',
      'actual_seconds','ai_reserved','ai_charge','claim_deadline','started_at','finished_at','max_pages'];
    const boolFields = ['upload_authorized','ai_known','pending_active','ai_enabled'];
    const result = { ...raw };
    for (const key of numberFields) if (result[key] !== undefined && result[key] !== '') result[key] = Number(result[key]);
    for (const key of boolFields) result[key] = result[key] === '1';
    for (const key of ['report','preview']) {
      if (result[key]) { try { result[key] = JSON.parse(result[key]); } catch { /* leave opaque bad value for caller */ } }
      else result[key] = null;
    }
    if (result.reservation_seconds !== undefined) result.max_seconds = result.reservation_seconds;
    if (result.ai_reserved !== undefined) result.ai_allowance_microusd = result.ai_reserved;
    return result;
  }

  async cleanup({ now = Date.now(), limit = 50 } = {}) {
    if (!Number.isSafeInteger(limit) || limit <= 0) throw new RangeError('limit must be positive');
    const result = await this.run('cleanup', [this.key('expiry'), this.key('upload-deadlines'),
      this.key('pending'), this.key('ai:reserved'), this.key('ai:spent')],
      [now, limit, this.retentionSeconds, this.namespace]);
    const rows=typeof result === 'string'?JSON.parse(result):result;
    return Array.isArray(rows)?rows:[];
  }

  async consumeChallenge(tokenHash, now = Date.now()) {
    if (typeof tokenHash !== 'string' || !tokenHash) return false;
    return Number(await this.run('consumeChallenge', [this.key(`challenge:${tokenHash}`)], [now])) === 1;
  }

  async markDeleted(id) {
    const current = await this.get(id);
    if (!current) return null;
    const result = await this.run('markDeleted', [this.key(`job:${id}`), this.key('pending'),
      this.key('ai:reserved'), this.key(`daily:${current.day_key}:seconds`),
      this.key('expiry'), this.key('upload-deadlines')], [this.namespace]);
    if (!result || Number(result[0]) !== 1) return null;
    return { id: result[1], inputPath: result[2] || null, outputPath: result[3] || null };
  }
}

export { SCRIPTS };
