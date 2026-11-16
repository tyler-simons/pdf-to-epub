import {createHash,createHmac,timingSafeEqual} from 'node:crypto';
import {isIP,BlockList} from 'node:net';
export const hash=value=>createHash('sha256').update(value).digest('hex');
export const equal=(a,b)=>timingSafeEqual(Buffer.from(hash(String(a))),Buffer.from(hash(String(b))));
export function identity(request,secret){
  // Vercel overwrites this header at its trusted edge. Do not use this adapter behind an arbitrary proxy.
  let ip=request.headers.get('x-vercel-forwarded-for')?.split(',')[0].trim()||'unknown';
  if(isIP(ip)===6){
    // Normalize expanded/compressed IPv6 through URL serialization, then group by /64.
    try{const canonical=new URL(`http://[${ip}]/`).hostname.slice(1,-1);const [a,b]=canonical.split('::');let words=a?a.split(':'):[];
      if(b!==undefined){const suffix=b?b.split(':'):[];words=[...words,...Array(8-words.length-suffix.length).fill('0'),...suffix]}
      ip=words.slice(0,4).map(w=>parseInt(w||'0',16).toString(16)).join(':')+'::/64';
    }catch{ip='unknown'}
  }else if(isIP(ip)!==4)ip='unknown';
  return createHmac('sha256',secret).update(ip).digest('hex');
}
export function internalAuth(request,secret){return equal(request.headers.get('authorization')||'','Bearer '+secret)}
export function checkOrigin(request,hostname){const origin=request.headers.get('origin');return !origin||origin===`https://${hostname}`}
export async function verifyChallenge(token,cfg,fetcher=fetch){
  if(typeof token!=='string'||!token||token.length>2048)return false;
  try{const response=await fetcher('https://challenges.cloudflare.com/turnstile/v0/siteverify',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({secret:cfg.challengeSecret,response:token}),signal:AbortSignal.timeout(8000)});
    if(!response.ok)return false;const body=await response.text();if(body.length>16384)return false;const value=JSON.parse(body);
    return value.success===true&&value.hostname===cfg.hostname&&value.action==='convert';
  }catch{return false}
}
