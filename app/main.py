"""Single-instance pilot API. Run one ASGI worker against a persistent data volume."""
import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import sqlite3
import sys
import time
import uuid
import zipfile
from xml.etree import ElementTree as ET

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import Settings
from .budget import Budget, BudgetExceeded
from .public_access import PublicAccess, AccessDenied

STATIC=Path(__file__).parent/'static'

def digest(value): return hashlib.sha256(value.encode()).hexdigest()

class UploadLimit:
    def __init__(self, app, limit): self.app,self.limit=app,limit
    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope.get('method')!='POST':
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        try: length=int(headers.get(b'content-length',b'0'))
        except ValueError: return await JSONResponse({'detail':'Invalid upload length'},400)(scope,receive,send)
        if length>self.limit: return await JSONResponse({'detail':'PDF exceeds upload limit'},413)(scope,receive,send)
        total=0
        async def bounded():
            nonlocal total
            message=await receive()
            total+=len(message.get('body',b''))
            if total>self.limit: raise HTTPException(413,'PDF exceeds upload limit')
            return message
        await self.app(scope,bounded,send)

class Store:
    def __init__(self, settings):
        self.settings=settings
        settings.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=settings.data_dir/'leaf.sqlite3'
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
             owner_hash TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
             created_at REAL NOT NULL, expires_at REAL NOT NULL, reservation TEXT NOT NULL,
             error TEXT, report TEXT)''')
        self.budget=Budget(self.db,total_ai_microusd=settings.total_ai_microusd,daily_jobs=settings.daily_jobs,
            daily_processing_seconds=settings.daily_processing_seconds,per_invite_daily_jobs=settings.per_client_daily_jobs)
    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.db,timeout=15);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def jobdir(self,id): return self.settings.data_dir/id
    def update(self,id,**values):
        with self.connect() as c:
            c.execute('UPDATE jobs SET '+','.join(f'{k}=?' for k in values)+' WHERE id=?',[*values.values(),id])
    def get(self,id):
        with self.connect() as c:return c.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
    def cleanup(self):
        with self.connect() as c:
            rows=c.execute("SELECT id FROM jobs WHERE expires_at<=? AND status NOT IN ('queued','processing','expired')",(time.time(),)).fetchall()
            for row in rows:
                shutil.rmtree(self.jobdir(row['id']),ignore_errors=True)
                c.execute("UPDATE jobs SET status='expired',stage='Expired',report=NULL,title='Expired book',error=NULL WHERE id=?",(row['id'],))
    def recover(self):
        with self.connect() as c:rows=c.execute("SELECT * FROM jobs WHERE status='processing'").fetchall()
        for row in rows:
            self.budget.finish_job(row['reservation'],self.settings.job_timeout_seconds)
            self.update(row['id'],status='failed',stage='Interrupted',error='The server restarted during conversion. Please try again.')

async def terminate(process):
    if process.returncode is not None:return
    with suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGKILL)
    await process.wait()

async def convert_job(store,row):
    cfg=store.settings;folder=store.jobdir(row['id']);started=time.monotonic();process=None
    store.update(row['id'],status='processing',stage='Rebuilding your book')
    try:
        env={key:value for key,value in os.environ.items() if key in ('PATH','LANG','LC_ALL','SYSTEMROOT','TESSDATA_PREFIX')}
        env.update(OMP_THREAD_LIMIT='1',OPENBLAS_NUM_THREADS='1',LEAF_CHILD_SECONDS=str(cfg.job_timeout_seconds))
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.runner',str(folder/'source.pdf'),str(folder/'book.epub'),
            '--report',str(folder/'report.json'),'--max-pages',str(cfg.max_pages),'--title',row['title'],
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,env=env,start_new_session=True)
        await asyncio.wait_for(process.wait(),timeout=cfg.job_timeout_seconds)
        if process.returncode:raise ValueError('Conversion failed')
        if cfg.ai_enabled:
            store.update(row['id'],stage='Checking suspicious text')
            ai_env=dict(env,OPENAI_API_KEY=cfg.api_key)
            process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.ai_worker',str(folder/'book.epub'),str(store.db),
                row['id'],str(cfg.total_ai_microusd),str(cfg.max_ai_per_job_microusd),str(folder/'ai-report.json'),
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,env=ai_env,start_new_session=True)
            remaining=max(1,cfg.job_timeout_seconds-(time.monotonic()-started))
            try:
                await asyncio.wait_for(process.wait(),timeout=remaining)
            except asyncio.TimeoutError:
                await terminate(process)
            if process.returncode==0 and (folder/'ai-report.json').is_file():
                ai_report=json.loads((folder/'ai-report.json').read_text())
            else:ai_report={'warnings':['Optional AI checking did not finish. Local conversion is retained.']}
        else:ai_report={'enabled':False}
        report=json.loads((folder/'report.json').read_text())
        report['ai']=ai_report
        report.setdefault('warnings',[]).extend(ai_report.get('warnings',[]))
        if not (folder/'book.epub').is_file():raise ValueError('Missing output')
        store.update(row['id'],status='ready',stage='Ready to read',report=json.dumps(report))
    except asyncio.CancelledError:
        if process:await terminate(process)
        store.update(row['id'],status='failed',stage='Interrupted',error='Conversion was interrupted. Please try again.')
        raise
    except asyncio.TimeoutError:
        if process:await terminate(process)
        store.update(row['id'],status='failed',stage='Time limit reached',error='This PDF exceeded the processing limit. Try a shorter book or a smaller section.')
    except Exception:
        if process:await terminate(process)
        store.update(row['id'],status='failed',stage='Could not convert',error='We could not convert this PDF. It may be encrypted, damaged, too large, or unsupported.')
    finally:
        store.budget.finish_job(row['reservation'],min(cfg.job_timeout_seconds,max(1,math.ceil(time.monotonic()-started))))
        (folder/'source.pdf').unlink(missing_ok=True)

async def worker(store):
    while True:
        store.cleanup()
        with store.connect() as c:
            row=c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row and row['expires_at']<=time.time():
            store.budget.finish_job(row['reservation'],0)
            shutil.rmtree(store.jobdir(row['id']),ignore_errors=True)
            store.update(row['id'],status='expired',stage='Expired',title='Expired book')
        elif row:await convert_job(store,row)
        else:await asyncio.sleep(.5)

async def sweeper(store):
    while True:
        store.cleanup();await asyncio.sleep(60)

def create_app(settings=None):
    cfg=settings or Settings();store=Store(cfg)
    access=PublicAccess(store.db,cfg.access_secret or 'local-development-only',requests_per_hour=cfg.requests_per_hour)
    @asynccontextmanager
    async def lifespan(app):
        store.recover();tasks=[asyncio.create_task(worker(store)),asyncio.create_task(sweeper(store))]
        try:yield
        finally:
            for task in tasks:task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):await task
    app=FastAPI(title='Leaf',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store
    app.add_middleware(UploadLimit,limit=cfg.max_upload_mb*1024*1024+128*1024)
    @app.middleware('http')
    async def security(request,call_next):
        origin=request.headers.get('origin')
        if request.method in ('POST','DELETE') and origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Cross-site uploads are not allowed.'},403)
        response=await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Cache-Control':'no-store','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; style-src 'self'; img-src 'self' blob:; connect-src 'self' https://challenges.cloudflare.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
        return response
    async def owner(request):
        if not cfg.public:return digest('local-development')
        identity=access.identify(request.client.host if request.client else 'unknown')
        try:access.admit_attempt(identity)
        except AccessDenied as exc:raise HTTPException(429,'Too many upload attempts. Please try again later.') from exc
        token=request.headers.get('X-Challenge-Token','')
        valid=await asyncio.to_thread(access.verify_challenge,token,cfg.turnstile_secret_key,cfg.public_hostname)
        if not valid:raise HTTPException(403,'Please complete the verification and try again.')
        if not access.consume_challenge(token):raise HTTPException(403,'Verification has expired or was already used. Please try again.')
        return identity
    def authorize(id,request):
        row=store.get(id)
        header=request.headers.get('Authorization','')
        if not row or not header.startswith('Bearer ') or not secrets.compare_digest(row['token_hash'],digest(header[7:])):
            raise HTTPException(404,'Book not found.')
        if row['expires_at']<=time.time() or row['status']=='expired':raise HTTPException(410,'This book has expired. Upload it again to convert.')
        return row
    @app.get('/api/config')
    async def config():return {'max_upload_mb':cfg.max_upload_mb,'max_pages':cfg.max_pages,'retention_hours':cfg.retention_hours,'invite_required':False,'public':cfg.public,'turnstile_site_key':cfg.turnstile_site_key if cfg.public else None,'ai_enabled':cfg.ai_enabled}
    @app.get('/healthz')
    async def health():return {'status':'ok'}
    @app.post('/api/jobs',status_code=202)
    async def upload(request:Request):
        owner_hash=await owner(request)
        with store.connect() as c:pending=c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','processing')").fetchone()[0]
        if pending>=cfg.max_pending_jobs:raise HTTPException(429,'The queue is full. Please try again later.')
        form=await request.form(max_files=1,max_fields=2,max_part_size=cfg.max_upload_mb*1024*1024)
        file=form.get('file');title=str(form.get('title') or '').strip()[:160]
        if not file or not hasattr(file,'read'):
            await form.close()
            raise HTTPException(400,'Choose a PDF file.')
        filename=str(file.filename or 'Book.pdf')
        if not filename.lower().endswith('.pdf'):
            await form.close()
            raise HTTPException(400,'Choose a PDF file.')
        id=uuid.uuid4().hex;folder=store.jobdir(id);folder.mkdir(mode=0o700);size=0
        try:
            with (folder/'source.pdf').open('wb') as dest:
                while block:=await file.read(65536):
                    size+=len(block)
                    if size>cfg.max_upload_mb*1024*1024:raise HTTPException(413,'PDF exceeds upload limit.')
                    dest.write(block)
            with (folder/'source.pdf').open('rb') as source:magic=source.read(1024)
            if b'%PDF-' not in magic:raise HTTPException(400,'This does not appear to be a PDF.')
            with store.connect() as c:pending=c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','processing')").fetchone()[0]
            if pending>=cfg.max_pending_jobs:raise HTTPException(429,'The queue is full. Please try again later.')
            try:reservation=store.budget.reserve_job(owner_hash,cfg.job_timeout_seconds)
            except BudgetExceeded as exc:raise HTTPException(429,str(exc)) from exc
            token=secrets.token_urlsafe(32);now=time.time();title=title or Path(filename).stem[:160] or 'Book'
            try:
                with store.connect() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (id,digest(token),owner_hash,title,'queued','Waiting for a free worker',now,now+cfg.retention_hours*3600,reservation,None,None))
            except Exception:
                store.budget.finish_job(reservation,0);raise
            return {'id':id,'token':token,'status':'queued'}
        except BaseException:
            shutil.rmtree(folder,ignore_errors=True);raise
        finally:await file.close()
    @app.get('/api/jobs/{id}')
    async def status(id:str,request:Request):
        row=authorize(id,request)
        return {key:row[key] for key in ('id','title','status','stage','error','created_at','expires_at')}|{'report':json.loads(row['report']) if row['report'] else None}
    @app.get('/api/jobs/{id}/preview')
    async def preview(id:str,request:Request):
        row=authorize(id,request)
        if row['status']!='ready':raise HTTPException(409,'Your book is not ready yet.')
        paragraphs=[]
        with zipfile.ZipFile(store.jobdir(id)/'book.epub') as z:
            for name in z.namelist():
                if not name.endswith('.xhtml') or 'nav' in name:continue
                root=ET.fromstring(z.read(name))
                for element in root.iter():
                    if element.tag.rsplit('}',1)[-1] in ('p','h1','h2','h3'):
                        text=''.join(element.itertext()).strip()
                        if text:paragraphs.append(text[:2000])
                    if len(paragraphs)>=24:break
                if len(paragraphs)>=24:break
        report=json.loads(row['report']) if row['report'] else {}
        return {'title':row['title'],'paragraphs':paragraphs,'warnings':report.get('warnings',[])}
    @app.get('/api/jobs/{id}/download')
    async def download(id:str,request:Request):
        row=authorize(id,request)
        if row['status']!='ready':raise HTTPException(409,'Your book is not ready yet.')
        filename=re.sub(r'[^\w .-]','',row['title'])[:100].strip() or 'Book'
        return FileResponse(store.jobdir(id)/'book.epub',media_type='application/epub+zip',filename=filename+'.epub')
    @app.delete('/api/jobs/{id}')
    async def delete(id:str,request:Request):
        row=authorize(id,request)
        if row['status'] in ('queued','processing'):raise HTTPException(409,'Wait until conversion finishes before deleting.')
        shutil.rmtree(store.jobdir(id),ignore_errors=True);store.update(id,status='expired',stage='Deleted',report=None,title='Deleted book')
        return {'status':'deleted'}
    @app.get('/')
    async def home():return FileResponse(STATIC/'index.html')
    app.mount('/static',StaticFiles(directory=STATIC),name='web')
    return app
