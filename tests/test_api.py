import io
import time
import zipfile
from pathlib import Path
import pytest
import pymupdf
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app


def pdf():
    doc=pymupdf.open();page=doc.new_page();page.insert_text((50,70),'A readable little book.');return doc.tobytes()


def test_private_conversion_download_preview_and_delete(tmp_path):
    app=create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        r=client.post('/api/jobs',files={'file':('../../secret.pdf',pdf())})
        assert r.status_code==202,r.text
        job=r.json();url='/api/jobs/'+job['id'];auth={'Authorization':'Bearer '+job['token']}
        assert client.get(url).status_code==404
        assert client.get(url,headers={'Authorization':'Bearer wrong'}).status_code==404
        for _ in range(100):
            status=client.get(url,headers=auth).json()
            if status['status'] in ('ready','failed'):break
            time.sleep(.1)
        assert status['status']=='ready',status
        assert client.get(url+'/preview',headers=auth).json()['paragraphs']
        r=client.get(url+'/download',headers=auth);assert r.status_code==200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:assert z.testzip() is None
        assert not (tmp_path/job['id']/'source.pdf').exists()
        assert client.delete(url,headers=auth).status_code==200
        assert not (tmp_path/job['id']).exists()
        assert client.get(url,headers=auth).status_code==410


def test_bad_pdf_and_size_limits(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path,max_upload_mb=1))) as client:
        assert client.post('/api/jobs',files={'file':('bad.pdf',b'hello')}).status_code==400
        assert client.post('/api/jobs',files={'file':('bad.txt',pdf())}).status_code==400
        assert client.post('/api/jobs',files={'file':('big.pdf',b'%PDF-'+b'x'*1200000)}).status_code==413


def test_expiry_removes_private_files(tmp_path):
    app=create_app(Settings(data_dir=tmp_path));store=app.state.store
    # No lifespan worker so the job can be transitioned deterministically.
    client=TestClient(app)
    job=client.post('/api/jobs',files={'file':('book.pdf',pdf())}).json()
    folder=tmp_path/job['id'];assert folder.exists()
    store.update(job['id'],status='failed',expires_at=time.time()-1)
    store.cleanup();assert not folder.exists()
    assert client.get('/api/jobs/'+job['id'],headers={'Authorization':'Bearer '+job['token']}).status_code==410


def test_public_mode_requires_bot_protection(tmp_path):
    with pytest.raises(ValueError):Settings(data_dir=tmp_path,public=True)


def test_daily_client_cap(tmp_path):
    client=TestClient(create_app(Settings(data_dir=tmp_path,per_client_daily_jobs=1)))
    assert client.post('/api/jobs',files={'file':('one.pdf',pdf())}).status_code==202
    assert client.post('/api/jobs',files={'file':('two.pdf',pdf())}).status_code==429


def test_static_assets_and_cross_site_uploads(tmp_path):
    client=TestClient(create_app(Settings(data_dir=tmp_path)))
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    assert client.get('/static/style.css').status_code==200
    assert client.post('/api/jobs',headers={'Origin':'https://unrelated.example'},files={'file':('book.pdf',pdf())}).status_code==403


def test_wall_timeout_kills_worker_and_removes_upload(tmp_path,monkeypatch):
    import asyncio
    import sys
    real=asyncio.create_subprocess_exec
    async def slow(*args,**kwargs):
        return await real(sys.executable,'-c','import time; time.sleep(30)',**kwargs)
    monkeypatch.setattr('app.main.asyncio.create_subprocess_exec',slow)
    app=create_app(Settings(data_dir=tmp_path,job_timeout_seconds=1))
    with TestClient(app) as client:
        job=client.post('/api/jobs',files={'file':('slow.pdf',pdf())}).json()
        headers={'Authorization':'Bearer '+job['token']}
        for _ in range(60):
            status=client.get('/api/jobs/'+job['id'],headers=headers).json()
            if status['status']=='failed':break
            time.sleep(.1)
        assert status['status']=='failed'
        assert status['stage']=='Time limit reached'
        assert not (tmp_path/job['id']/'source.pdf').exists()


def public_settings(tmp_path,**overrides):
    return Settings(data_dir=tmp_path,public=True,turnstile_site_key='test-site',turnstile_secret_key='test-secret',
        public_hostname='testserver',access_secret='x'*32,**overrides)


def test_public_challenge_required_and_not_reusable(tmp_path,monkeypatch):
    monkeypatch.setattr('app.main.PublicAccess.verify_challenge',lambda self,token,*args:token=='accepted')
    client=TestClient(create_app(public_settings(tmp_path)))
    config=client.get('/api/config').json()
    assert config['invite_required'] is False and config['turnstile_site_key']=='test-site'
    assert 'test-secret' not in str(config) and 'x'*32 not in str(config)
    assert client.post('/api/jobs',files={'file':('book.pdf',pdf())}).status_code==403
    headers={'X-Challenge-Token':'accepted'}
    assert client.post('/api/jobs',headers=headers,files={'file':('book.pdf',pdf())}).status_code==202
    assert client.post('/api/jobs',headers=headers,files={'file':('book.pdf',pdf())}).status_code==403


def test_public_failed_attempts_are_limited_before_parsing(tmp_path,monkeypatch):
    monkeypatch.setattr('app.main.PublicAccess.verify_challenge',lambda *args:False)
    client=TestClient(create_app(public_settings(tmp_path,requests_per_hour=2)))
    assert client.post('/api/jobs').status_code==403
    assert client.post('/api/jobs').status_code==403
    assert client.post('/api/jobs').status_code==429
