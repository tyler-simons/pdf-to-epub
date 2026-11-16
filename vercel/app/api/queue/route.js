import {handleCallback} from '@vercel/queue';
import {runtime as getRuntime} from '../../../lib/runtime.mjs';
export const maxDuration=300;
export const runtime='nodejs';
export const POST=handleCallback(async message=>{
  if(!message||!/^[a-f0-9]{32}$/.test(message.job_id||''))return;
  const {cfg,store}=getRuntime();const job=await store.get(message.job_id);
  if(!job||['ready','failed','expired','deleted'].includes(job.status))return;
  const response=await fetch(cfg.converterUrl.replace(/\/$/,'')+'/internal/convert',{method:'POST',
    headers:{'Content-Type':'application/json',Authorization:'Bearer '+cfg.workerSecret},body:JSON.stringify({job_id:message.job_id}),
    signal:AbortSignal.timeout(280000)});
  if(!response.ok)throw new Error('Converter invocation failed');
},{visibilityTimeoutSeconds:300,retry:(error,metadata)=>metadata.deliveryCount>=5?{acknowledge:true}:{afterSeconds:30}});
