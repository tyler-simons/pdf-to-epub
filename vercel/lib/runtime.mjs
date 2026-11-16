import {Redis} from '@upstash/redis';
import * as blob from '@vercel/blob';
import {handleUpload} from '@vercel/blob/client';
import {send} from '@vercel/queue';
import {Store} from './store.mjs';
import {config} from './config.mjs';
import {createGateway} from './gateway.mjs';
let singleton;
export function runtime(){
  if(singleton)return singleton;
  const cfg={...config(),cronSecret:process.env.CRON_SECRET||''};
  const redis=new Redis({url:process.env.UPSTASH_REDIS_REST_URL,token:process.env.UPSTASH_REDIS_REST_TOKEN,automaticDeserialization:false});
  const store=new Store(redis,{namespace:'leaf:{v1}',aiCap:cfg.aiCap,aiEnabled:cfg.aiEnabled,aiPerJob:cfg.perJobAI,dailyJobs:cfg.dailyJobs,
    perClientJobs:cfg.perClientJobs,dailySeconds:cfg.dailySeconds,maxPending:cfg.maxPending,jobSeconds:cfg.jobSeconds});
  singleton={cfg,store,handle:createGateway({cfg,store,blob,uploadHandler:handleUpload,send})};return singleton;
}
