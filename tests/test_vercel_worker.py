import asyncio
import json
import signal
import subprocess
import sys
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
from app import vercel_worker as worker


JOB_ID = str(uuid.uuid4())
SECRET = "test-secret-that-is-long-enough-to-use-here"


class FakeDownload:
    def __init__(self, chunks):
        self.chunks = chunks
        self.metadata = type("Metadata", (), {"size": sum(map(len, chunks))})()

    def __aiter__(self):
        self.i = 0
        return self

    async def __anext__(self):
        if self.i >= len(self.chunks):
            raise StopAsyncIteration
        chunk = self.chunks[self.i]
        self.i += 1
        return chunk


class FakeBlob:
    def __init__(self, chunks=(b"%PDF-1.7\nfixture",)):
        self.chunks = chunks
        self.deleted = []
        self.uploaded = {}
        self.advertised_size = None

    @asynccontextmanager
    async def stream_blob(self, pathname, *, access):
        assert access == "private"
        assert pathname == f"jobs/{JOB_ID}/source.pdf"
        download = FakeDownload(self.chunks)
        if self.advertised_size is not None:
            download.metadata.size = self.advertised_size
        yield download

    async def delete_blob(self, pathname):
        self.deleted.append(pathname)

    async def put_blob(self, pathname, body, **kwargs):
        self.uploaded[pathname] = (body, kwargs)


def _job(**overrides):
    value = {"id": JOB_ID, "title": "Worker test", "input_path": f"jobs/{JOB_ID}/source.pdf",
             "expires_at": "2099-01-01T00:00:00Z", "max_pages": 10, "max_seconds": 90,
             "ai_enabled": False, "ai_allowance_microusd": 0}
    value.update(overrides)
    return value


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("LEAF_WORKER_SECRET", SECRET)
    monkeypatch.setenv("LEAF_GATEWAY_URL", "https://gateway.example")


def test_authentication_and_bad_payload():
    client = TestClient(worker.app)
    assert client.post("/internal/convert", json={"job_id": JOB_ID}).status_code == 401
    assert client.post("/internal/convert", headers={"Authorization": f"Bearer {SECRET}"}, json={"job_id":"arbitrary"}).status_code == 400


def test_gateway_compact_job_ids_and_unicode_auth_are_safe(monkeypatch):
    compact_id = "0123456789abcdef0123456789abcdef"
    assert worker._JOB_ID.fullmatch(compact_id)
    assert worker._authorized("Bearer \N{SNOWMAN}", SECRET) is False
    monkeypatch.setattr(worker, "_post_json", lambda *args, **kwargs: {"claimed": False})
    response = TestClient(worker.app).post("/internal/convert", headers={"Authorization": f"Bearer {SECRET}"},
                                           json={"job_id": compact_id})
    assert response.status_code == 200


def test_not_claimed_is_acknowledged_without_blob_access(monkeypatch):
    called = []
    monkeypatch.setattr(worker, "_post_json", lambda *args, **kwargs: {"claimed": False})
    monkeypatch.setattr(worker, "_blob_module", lambda: called.append(True))
    response = TestClient(worker.app).post("/internal/convert", headers={"Authorization": f"Bearer {SECRET}"}, json={"job_id": JOB_ID})
    assert response.status_code == 200
    assert response.json() == {"claimed": False}
    assert not called


def test_worker_claims_streams_converts_uploads_and_finishes(monkeypatch, tmp_path):
    blob = FakeBlob()
    monkeypatch.setattr(worker, "_blob_module", lambda: _async_value(blob))
    claims, finishes = [], []

    def gateway(url, secret, body, timeout=10):
        assert secret == SECRET
        if url.endswith("/claim"):
            claims.append(body)
            return {"claimed": True, "claim_token": "claim-secret", "job": _job()}
        finishes.append(body)
        return {"ok": True}

    monkeypatch.setattr(worker, "_post_json", gateway)

    def converter(source, output, report, job, timeout):
        assert source.read_bytes().startswith(b"%PDF-")
        assert job["id"] == JOB_ID
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("EPUB/text/p1.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Preview paragraph.</p></body></html>')
        report.write_text(json.dumps({"converted_pages": 1, "warnings": []}))

    monkeypatch.setattr(worker, "_run_converter", converter)
    response = TestClient(worker.app).post("/internal/convert", headers={"Authorization": f"Bearer {SECRET}"}, json={"job_id": JOB_ID})
    assert response.status_code == 200, response.text
    assert claims == [{"job_id": JOB_ID}]
    assert blob.deleted == [f"jobs/{JOB_ID}/source.pdf"]
    output = f"jobs/{JOB_ID}/book.epub"
    assert output in blob.uploaded
    assert blob.uploaded[output][1]["access"] == "private"
    assert blob.uploaded[output][1]["allow_overwrite"] is False
    assert finishes[0]["status"] == "ready"
    assert finishes[0]["claim_token"] == "claim-secret"
    assert finishes[0]["preview"] == ["Preview paragraph."]
    assert finishes[0]["processing_seconds"] >= 0


async def _async_value(value):
    return value


def test_invalid_pdf_is_terminal_and_input_is_deleted(monkeypatch):
    blob = FakeBlob((b"not pdf",))
    monkeypatch.setattr(worker, "_blob_module", lambda: _async_value(blob))
    finished = []
    def gateway(url, secret, body, timeout=10):
        if url.endswith("/claim"):
            return {"claimed": True, "claim_token": "token", "job": _job()}
        finished.append(body)
        return {}
    monkeypatch.setattr(worker, "_post_json", gateway)
    response = TestClient(worker.app).post("/internal/convert", headers={"Authorization": f"Bearer {SECRET}"}, json={"job_id": JOB_ID})
    assert response.status_code == 200
    assert finished[0]["status"] == "failed"
    assert finished[0]["error"] == "invalid_pdf"
    assert finished[0]["output_path"] is None
    assert blob.deleted == [f"jobs/{JOB_ID}/source.pdf"]


def test_job_record_cannot_choose_arbitrary_blob_path():
    with pytest.raises(ValueError, match="input object"):
        worker._valid_job(_job(input_path="https://example.invalid/file.pdf"), JOB_ID)


def test_streaming_size_cap_applies_before_full_write(tmp_path):
    blob = FakeBlob((b"%PDF-1.7", b"x" * (worker.MAX_INPUT_BYTES + 1)))
    blob.advertised_size = 8
    target = tmp_path / "input.pdf"
    with pytest.raises(ValueError, match="input size"):
        asyncio.run(worker._fetch_input(blob, f"jobs/{JOB_ID}/source.pdf", target))
    assert target.stat().st_size <= worker.MAX_INPUT_BYTES


def test_converter_timeout_kills_entire_process_group(monkeypatch, tmp_path):
    calls = []

    class Process:
        pid = 4321
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("converter", timeout)
            calls.append("reaped")
            return -signal.SIGKILL

    def popen(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        return Process()

    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    monkeypatch.setattr(worker.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    with pytest.raises(TimeoutError, match="timed out"):
        worker._run_converter(tmp_path / "source.pdf", tmp_path / "out.epub", tmp_path / "report.json", _job(), 0.01)
    assert calls == [(4321, signal.SIGKILL), "reaped"]

@pytest.mark.parametrize('code', ['page_limit', 'password_required', 'empty_pdf'])
def test_converter_preserves_actionable_failure(monkeypatch, tmp_path, code):
    report = tmp_path / 'report.json'
    report.write_text(json.dumps({'error_code': code}))
    class FailedProcess:
        def wait(self, timeout=None):
            return 1
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: FailedProcess())
    with pytest.raises(worker.ConversionFailure, match=code):
        worker._run_converter(tmp_path / 'input.pdf', tmp_path / 'book.epub', report, _job(), 10)
