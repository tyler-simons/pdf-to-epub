import {readFile} from 'node:fs/promises';
import path from 'node:path';
export const dynamic='force-dynamic';
export async function GET(){return new Response(await readFile(path.join(process.cwd(),'public/index.html')),{headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}})}
