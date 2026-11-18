export function config(env=process.env){
  const integer=(key,fallback)=>{const n=Number(env[key]??fallback);if(!Number.isSafeInteger(n)||n<0)throw new Error('Invalid '+key);return n};
  const result={hostname:env.LEAF_PUBLIC_HOSTNAME||'',accessSecret:env.LEAF_ACCESS_SECRET||'',workerSecret:env.LEAF_WORKER_SECRET||'',
    converterUrl:env.LEAF_CONVERTER_URL||'',siteKey:env.TURNSTILE_SITE_KEY||'',challengeSecret:env.TURNSTILE_SECRET_KEY||'',
    aiEnabled:env.LEAF_AI_ENABLED==='true',aiCap:integer('LEAF_AI_BUDGET_MICROUSD',9000000),perJobAI:integer('LEAF_AI_PER_JOB_MICROUSD',100000),
    maxBytes:40*1024*1024,maxPages:1500,jobSeconds:240,retentionSeconds:86400,dailyJobs:integer('LEAF_DAILY_JOBS',20),
    perClientJobs:integer('LEAF_PER_CLIENT_DAILY_JOBS',3),dailySeconds:integer('LEAF_DAILY_PROCESSING_SECONDS',3600),maxPending:10};
  if(!result.hostname||result.accessSecret.length<32||result.workerSecret.length<32||!result.siteKey||!result.challengeSecret||
    !env.UPSTASH_REDIS_REST_URL||!env.UPSTASH_REDIS_REST_TOKEN||!env.BLOB_READ_WRITE_TOKEN)throw new Error('Vercel app setup is incomplete');
  const worker=new URL(result.converterUrl);if(worker.protocol!=='https:'||worker.username||worker.password||worker.search||worker.hash)throw new Error('Invalid worker URL');
  return result;
}
