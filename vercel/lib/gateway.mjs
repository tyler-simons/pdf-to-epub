import {randomBytes} from 'node:crypto';
import {hash,equal,identity,internalAuth,checkOrigin,verifyChallenge} from './security.mjs';

export class HTTPError extends Error {constructor(status,message){super(message);this.status=status}}
const json=(body,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store'}});
const idPattern=/^[a-f0-9]{32}$/;
async function body(request,limit=65536){
  if(Number(request.headers.get('content-length')||0)>limit)throw new HTTPError(413,'Request too large.');
  const text=await request.text();if(Buffer.byteLength(text)>limit)throw new HTTPError(413,'Request too large.');
  try{return JSON.parse(text)}catch{throw new HTTPError(400,'Invalid request.')}
}
function safeReport(value){
  if(!value||typeof value!=='object'||Array.isArray(value))return {};
  const result={};for(const key of ['converted_pages','source_pages','ocr_pages','image_count','paragraph_count'])if(Number.isSafeInteger(value[key])&&value[key]>=0)result[key]=value[key];
  result.warnings=Array.isArray(value.warnings)?value.warnings.filter(x=>typeof x==='string').slice(0,50).map(x=>x.slice(0,500)):[];
  if(value.ai&&typeof value.ai==='object')result.ai={enabled:value.ai.enabled===true,calls:Number(value.ai.calls)||0,corrections:Number(value.ai.corrections)||0};
  return result;
}
export function createGateway({cfg,store,blob,uploadHandler,send,challenge=verifyChallenge,clock=()=>Date.now()}){
  async function authorize(request,id,{expired=false}={}){
    if(!idPattern.test(id))throw new HTTPError(404,'Book not found.');
    const record=await store.get(id);const authorization=request.headers.get('authorization')||'';
    if(!record||!authorization.startsWith('Bearer ')||!equal(record.token_hash,hash(authorization.slice(7))))throw new HTTPError(404,'Book not found.');
    if(!expired&&(record.expires_at<=clock()||['expired','deleted'].includes(record.status)))throw new HTTPError(410,'This book has expired.');
    return record;
  }
  async function finalize(id){
    const record=await store.get(id);if(!record)throw new HTTPError(404,'Book not found.');
    if(record.expires_at<=clock()||['expired','deleted'].includes(record.status)){await blob.del(record.input_path);return record}
    if(record.status==='uploading'){
      const metadata=await blob.head(record.input_path);
      if(metadata.pathname!==record.input_path||metadata.size!==record.expected_size||metadata.size>cfg.maxBytes||metadata.contentType!=='application/pdf')throw new HTTPError(400,'Upload did not match the PDF request.');
      await store.finalizeUpload({id,inputPath:record.input_path,size:metadata.size,now:clock()});
    }
    const updated=await store.get(id);
    if(updated.status==='queued')await send('leaf-convert',{job_id:id},{idempotencyKey:'leaf-'+id,retentionSeconds:86400});
    return updated;
  }
  async function cleanup(limit=10){const due=await store.cleanup({now:clock(),limit});if(due.length)await blob.del(due.flatMap(j=>[`jobs/${j.id}/source.pdf`,`jobs/${j.id}/book.epub`]));return due;}
  return async function handle(request){
    try{
      const path=new URL(request.url).pathname;const method=request.method;
      if(['POST','DELETE'].includes(method)&&!checkOrigin(request,cfg.hostname))throw new HTTPError(403,'Cross-site uploads are not allowed.');
      if(path==='/api/config'&&method==='GET')return json({max_upload_mb:40,max_pages:cfg.maxPages,retention_hours:24,public:true,ai_enabled:cfg.aiEnabled,turnstile_site_key:cfg.siteKey,upload_mode:'direct'});
      if(path==='/api/jobs'&&method==='POST'){
        const ownerHash=identity(request,cfg.accessSecret);const admitted=await store.attempt(ownerHash,clock());if(!admitted.admitted)throw new HTTPError(429,'Too many upload attempts. Please try again later.');
        const token=request.headers.get('x-challenge-token');
        if(!await challenge(token,cfg)||!await store.consumeChallenge(hash(token||'')))throw new HTTPError(403,'Complete the browser verification and try again.');
        await cleanup();
        const value=await body(request,4096);
        if(typeof value.filename!=='string'||!value.filename.toLowerCase().endsWith('.pdf')||!Number.isSafeInteger(value.size)||value.size<5||value.size>cfg.maxBytes)throw new HTTPError(400,'Choose a PDF up to 40 MB.');
        const id=randomBytes(16).toString('hex'),tokenValue=randomBytes(32).toString('base64url');
        const title=(typeof value.title==='string'&&value.title.trim()?value.title.trim():value.filename.replace(/\.pdf$/i,'')).slice(0,160);
        const record=await store.createJob({id,tokenHash:hash(tokenValue),ownerHash,title,now:clock(),bytes:value.size});
        return json({id,token:tokenValue,status:'uploading',input_path:record.input_path},202);
      }
      if(path==='/api/blob-upload'&&method==='POST'){
        const value=await body(request);
        if(!['blob.generate-client-token','blob.upload-completed'].includes(value.type))throw new HTTPError(400,'Invalid upload request.');
        const response=await uploadHandler({body:value,request,
          onBeforeGenerateToken:async(pathname,clientPayload,multipart)=>{
            let payload;try{payload=JSON.parse(clientPayload||'{}')}catch{throw new HTTPError(400,'Invalid upload request.')}
            const record=await authorize(request,payload.id||'');
            if(pathname!==record.input_path||multipart)throw new HTTPError(400,'Invalid upload destination.');
            if(!await store.uploadAuthorization({id:record.id,tokenHash:record.token_hash,now:clock()}))throw new HTTPError(409,'Upload authorization already used or expired.');
            return {allowedContentTypes:['application/pdf'],maximumSizeInBytes:record.expected_size,addRandomSuffix:false,allowOverwrite:false,
              validUntil:clock()+300000,ttlDays:1,tokenPayload:JSON.stringify({id:record.id})};
          },
          onUploadCompleted:async({blob:uploaded,tokenPayload})=>{
            const payload=JSON.parse(tokenPayload||'{}');if(!idPattern.test(payload.id||''))throw new HTTPError(400,'Invalid upload callback.');
            const record=await store.get(payload.id);if(!record||uploaded.pathname!==record.input_path)throw new HTTPError(400,'Invalid upload callback.');
            await finalize(record.id);
          }});
        return json(response);
      }
      const match=path.match(/^\/api\/jobs\/([a-f0-9]{32})(?:\/(finalize|preview|download))?$/);
      if(match){
        const [,id,action]=match;const record=await authorize(request,id);
        if(action==='finalize'&&method==='POST'){
          const value=await body(request,1024);if(value.pathname!==record.input_path)throw new HTTPError(400,'Invalid upload destination.');
          const updated=await finalize(id);return json({id,status:updated.status});
        }
        if(!action&&method==='GET'&&record.status==='processing'&&record.claim_deadline<=clock()){await store.claim({id,now:clock()});const latest=await store.get(id);return json({id,title:latest.title,status:latest.status,stage:latest.stage,error:latest.error||null,created_at:latest.created_at/1000,expires_at:latest.expires_at/1000,report:latest.report||null})}
        if(!action&&method==='GET')return json({id,title:record.title,status:record.status,stage:record.stage||({uploading:'Uploading PDF',queued:'Queued',processing:'Converting PDF',ready:'Ready to read',failed:'Conversion failed'}[record.status]||'Unavailable'),
          created_at:record.created_at/1000,expires_at:record.expires_at/1000,error:record.error||null,report:record.report||null});
        if(!action&&method==='DELETE'){
          if(['queued','processing'].includes(record.status))throw new HTTPError(409,'Wait until conversion finishes before deleting.');
          await store.markDeleted(id);await blob.del([record.input_path,`jobs/${id}/book.epub`]);return json({status:'deleted'});
        }
        if(record.status!=='ready')throw new HTTPError(409,'Your EPUB is not ready yet.');
        if(action==='preview'&&method==='GET')return json({title:record.title,paragraphs:record.preview||[],warnings:record.report?.warnings||[]});
        if(action==='download'&&method==='GET'){
          const result=await blob.get(`jobs/${id}/book.epub`,{access:'private',useCache:false});
          if(!result||result.statusCode!==200)throw new HTTPError(404,'Download not found.');
          return new Response(result.stream,{headers:{'Content-Type':'application/epub+zip','Content-Disposition':'attachment; filename="Book.epub"','Cache-Control':'private, no-store'}});
        }
      }
      if(path.startsWith('/api/internal/')&&method==='POST'){
        if(!internalAuth(request,cfg.workerSecret))throw new HTTPError(404,'Not found.');
        const value=await body(request,150000);if(!idPattern.test(value.job_id||''))throw new HTTPError(400,'Invalid job.');
        if(path==='/api/internal/claim'){
          const claim=await store.claim({id:value.job_id,now:clock()});
          if(claim.claimed){claim.job={id:claim.job.id,title:claim.job.title,input_path:claim.job.input_path,expires_at:claim.job.expires_at/1000,max_pages:cfg.maxPages,max_seconds:cfg.jobSeconds,ai_enabled:claim.job.ai_enabled===true||claim.job.ai_enabled==='1',ai_allowance_microusd:claim.job.ai_reserved,claim_token:claim.claimToken};}
          return json({...claim,claim_token:claim.claimToken});
        }
        if(path==='/api/internal/finish'){
          if(!['ready','failed'].includes(value.status))throw new HTTPError(400,'Invalid status.');
          if(value.status==='ready'&&value.output_path!==`jobs/${value.job_id}/book.epub`)throw new HTTPError(400,'Invalid output path.');
          if(value.status==='ready'){
            const results=await blob.setTtl([value.output_path],{ttlDays:1});
            if(results.some(r=>r.error))throw new HTTPError(503,'Could not set output expiry.');
          }
          const preview=Array.isArray(value.preview)?value.preview.filter(x=>typeof x==='string').slice(0,24).map(x=>x.slice(0,2000)):[];
          await store.finish({id:value.job_id,claimToken:value.claim_token,status:value.status,report:safeReport(value.report),preview,
            aiCharge:value.ai_charge_microusd,aiKnown:Number.isSafeInteger(value.ai_charge_microusd),seconds:Math.min(cfg.jobSeconds,value.processing_seconds),outputPath:value.output_path||null,
            error:value.status==='failed'?'This PDF could not be converted within the pilot limits.':null,now:clock()});
          return json({ok:true});
        }
      }
      if(path==='/api/cleanup'&&method==='GET'){
        if(!cfg.cronSecret||!internalAuth(request,cfg.cronSecret))throw new HTTPError(404,'Not found.');
        const due=await cleanup(50);
        return json({deleted:due.length});
      }
      throw new HTTPError(404,'Not found.');
    }catch(error){
      if(error instanceof HTTPError)return json({detail:error.message},error.status);
      if(/limit|budget|queue.*full|allowance|exhaust/i.test(error.message||''))return json({detail:'The pilot has reached a usage limit. Please try again later.'},429);
      return json({detail:'The service could not complete this request. Please try again.'},503);
    }
  };
}
