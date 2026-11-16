import {runtime as getRuntime} from '../../../lib/runtime.mjs';
export const dynamic='force-dynamic';
export const runtime='nodejs';
async function handle(request){
 try{return await getRuntime().handle(request)}catch{return Response.json({detail:'The app has not been configured yet.'},{status:503,headers:{'Cache-Control':'no-store'}})}
}
export {handle as GET,handle as POST,handle as DELETE};
