import { cp, mkdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const here = path.dirname(fileURLToPath(import.meta.url));
const appStatic = path.resolve(here, '../app/static');
const publicRoot = path.join(here, 'public');
const publicStatic = path.join(publicRoot, 'static');

await mkdir(publicRoot, { recursive: true });
await rm(publicStatic, { recursive: true, force: true });
await cp(appStatic, publicStatic, { recursive: true });
await cp(path.join(appStatic, 'index.html'), path.join(publicRoot, 'index.html'));
await build({
  entryPoints: [path.join(here, 'browser-upload.js')],
  outfile: path.join(publicStatic, 'blob-upload.js'),
  bundle: true,
  format: 'iife',
  target: ['es2020'],
  platform: 'browser',
});
