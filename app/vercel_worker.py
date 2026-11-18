"""Stateless HTTP worker for private Vercel Blob PDF jobs."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree as ET

from fastapi import FastAPI, Header, HTTPException, Request as FastAPIRequest
from fastapi.responses import JSONResponse

MAX_INPUT_BYTES = 40 * 1024 * 1024
MAX_WORKER_SECONDS = 240
MAX_PAGES = 1500
MAX_PREVIEW_PARAGRAPHS = 24
MAX_PREVIEW_CHARS = 2000
_JOB_ID = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.I)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _config() -> tuple[str, str]:
    secret = os.environ.get("LEAF_WORKER_SECRET", "")
    gateway = os.environ.get("LEAF_GATEWAY_URL", "").rstrip("/")
    if len(secret) < 32:
        raise RuntimeError("worker secret is not configured")
    if not gateway or any(c in gateway for c in "?#"):
        raise RuntimeError("gateway URL is not configured")
    parsed = urlsplit(gateway)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local_http:
        raise RuntimeError("gateway URL must use HTTPS")
    if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise RuntimeError("gateway URL must be an origin")
    return gateway, secret


def _authorized(header: str | None, secret: str) -> bool:
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].encode("utf-8"), secret.encode("utf-8"))


def _post_json(url: str, secret: str, data: dict, timeout: float = 10.0) -> dict:
    request = Request(url, data=json.dumps(data, separators=(",", ":")).encode(), method="POST",
                      headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"})
    try:
        # Never forward the internal bearer secret to a redirect target.
        opener = build_opener(type("NoRedirect", (HTTPRedirectHandler,), {"redirect_request": lambda self, req, fp, code, msg, headers, newurl: None})())
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(256 * 1024 + 1)
            if len(payload) > 256 * 1024:
                raise ValueError("gateway response too large")
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("invalid gateway response")
            return value
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("gateway request failed") from exc


def _valid_job(job: object, job_id: str) -> dict:
    if not isinstance(job, dict) or job.get("id") != job_id:
        raise ValueError("invalid claimed job")
    required = ("title", "input_path", "expires_at", "max_pages", "max_seconds", "ai_enabled", "ai_allowance_microusd")
    if any(key not in job for key in required):
        raise ValueError("incomplete claimed job")
    # The gateway's trusted record may name only this job's fixed input object.
    if job["input_path"] != f"jobs/{job_id}/source.pdf":
        raise ValueError("invalid input object")
    pages, seconds, allowance = job["max_pages"], job["max_seconds"], job["ai_allowance_microusd"]
    if not isinstance(pages, int) or isinstance(pages, bool) or not 1 <= pages <= MAX_PAGES:
        raise ValueError("invalid page limit")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= MAX_WORKER_SECONDS:
        raise ValueError("invalid time limit")
    if not isinstance(allowance, int) or isinstance(allowance, bool) or not 0 <= allowance <= 2_000_000:
        raise ValueError("invalid AI allowance")
    if not isinstance(job["ai_enabled"], bool):
        raise ValueError("invalid AI flag")
    if not isinstance(job["title"], str):
        raise ValueError("invalid title")
    return job


async def _blob_module():
    from vercel import blob
    return blob


async def _fetch_input(blob, pathname: str, target: Path) -> int:
    total = 0
    prefix = bytearray()
    signature_checked = False
    async with blob.stream_blob(pathname, access="private") as download:
        size = download.metadata.size
        if size is not None and (size <= 0 or size > MAX_INPUT_BYTES):
            raise ValueError("input size rejected")
        with target.open("wb") as stream:
            async for chunk in download:
                if not isinstance(chunk, bytes):
                    raise ValueError("invalid input stream")
                total += len(chunk)
                if total > MAX_INPUT_BYTES:
                    raise ValueError("input size rejected")
                if not signature_checked:
                    prefix.extend(chunk)
                    if len(prefix) < 5:
                        continue
                    if not prefix.startswith(b"%PDF-"):
                        raise ValueError("input is not a PDF")
                    stream.write(prefix)
                    prefix.clear()
                    signature_checked = True
                else:
                    stream.write(chunk)
        if not signature_checked:
            raise ValueError("input is not a PDF")
    if total == 0:
        raise ValueError("empty input")
    return total


async def _delete_blob(blob, pathname: str) -> None:
    try:
        await blob.delete_blob(pathname)
    except Exception:
        # Expiry cleanup remains the gateway's fallback; never mask the job result.
        pass


def _preview(epub_path: Path) -> list[str]:
    result = []
    with zipfile.ZipFile(epub_path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith((".xhtml", ".html", ".htm")) or name.endswith("nav.xhtml"):
                continue
            try:
                root = ET.fromstring(archive.read(name))
            except (ET.ParseError, KeyError):
                continue
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1].lower() != "p":
                    continue
                text = " ".join("".join(element.itertext()).split())
                if text:
                    result.append(text[:MAX_PREVIEW_CHARS])
                    if len(result) >= MAX_PREVIEW_PARAGRAPHS:
                        return result
    return result


class _AllowanceBudget:
    """Adapter for the existing cleanup service; gateway already reserved the cap."""
    def __init__(self, allowance: int):
        self.allowance = allowance
        self.reserved = 0
        self.reservation: str | None = None
        self.actual: int | None = None

    def reserve_ai(self, job_id: str, maximum_microusd: int) -> str:
        if maximum_microusd > self.allowance or self.reserved:
            from .budget import BudgetExceeded
            raise BudgetExceeded("gateway AI allowance exceeded")
        self.reserved = maximum_microusd
        self.reservation = job_id
        return job_id

    def settle_ai(self, reservation: str, actual_microusd: int) -> None:
        if reservation == self.reservation and actual_microusd <= self.reserved:
            self.actual = actual_microusd

    @property
    def charge(self) -> int:
        # A failed/ambiguous provider call may have been billed: count its full reserve.
        return self.actual if self.actual is not None else self.reserved


class ConversionFailure(ValueError):
    pass


def _run_converter(source: Path, output: Path, report: Path, job: dict, timeout: float) -> None:
    args = [sys.executable, "-m", "app.runner", str(source), str(output),
            "--report", str(report), "--max-pages", str(job["max_pages"]), "--title", job["title"][:240]]
    child_env = {key: os.environ[key] for key in ("PATH", "PYTHONPATH", "PYTHONHOME", "LANG", "LC_ALL", "OMP_THREAD_LIMIT") if key in os.environ}
    child_env["LEAF_CHILD_SECONDS"] = str(max(1, int(timeout)))
    process = subprocess.Popen(args, cwd=Path(__file__).resolve().parent.parent, env=child_env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise TimeoutError("conversion timed out") from exc
    if return_code != 0 or not output.is_file() or not report.is_file():
        if report.is_file():
            try:
                code = json.loads(report.read_text(encoding='utf-8')).get('error_code')
                if code in {'page_limit', 'password_required', 'empty_pdf'}:
                    raise ConversionFailure(code)
            except (OSError, json.JSONDecodeError):
                pass
        raise ValueError("conversion failed")


async def _process(job_id: str, job: dict, claim_token: str, gateway: str, secret: str, started: float) -> dict:
    blob = None
    outcome = {"status": "failed", "output_path": None, "report": None, "preview": [],
               "ai_charge_microusd": 0, "error": "conversion_failed"}
    input_path = job["input_path"]
    temp_dir = None
    try:
        blob = await _blob_module()
        temp_dir = tempfile.TemporaryDirectory(prefix="leaf-job-", dir="/tmp")
        work = Path(temp_dir.name)
        source, output, report_path = work / "source.pdf", work / "book.epub", work / "report.json"
        await asyncio.wait_for(_fetch_input(blob, input_path, source),
                               timeout=min(45, job["max_seconds"]))
        elapsed = time.monotonic() - started
        ai_requested = job["ai_enabled"] and bool(os.environ.get("OPENAI_API_KEY")) and job["ai_allowance_microusd"] > 0
        remaining = min(job["max_seconds"], MAX_WORKER_SECONDS) - elapsed - (50 if ai_requested else 35)
        if remaining < 1:
            raise TimeoutError("job time limit reached")
        await asyncio.to_thread(_run_converter, source, output, report_path, job, remaining)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        warnings = list(report.get("warnings", []))
        ai_charge = 0
        api_key = os.environ.get("OPENAI_API_KEY", "") if job["ai_enabled"] else ""
        if api_key and job["ai_allowance_microusd"] > 0:
            from .ai import cleanup_epub
            budget = _AllowanceBudget(job["ai_allowance_microusd"])
            try:
                remaining = min(job["max_seconds"], MAX_WORKER_SECONDS) - (time.monotonic() - started) - 10
                if remaining < 21:
                    warnings.append("Optional text cleanup skipped to stay within the job time limit.")
                else:
                    # Existing cleanup has one provider call with a fixed 20s timeout.
                    # Await it to completion so its file rewrite cannot race the upload.
                    ai_result = await asyncio.to_thread(cleanup_epub, output, budget, job_id, api_key,
                                                        max_cost_microusd=job["ai_allowance_microusd"])
                    warnings.extend(ai_result.get("warnings", []))
                    report["ai_cleanup"] = {"enabled": True, "calls": ai_result.get("calls", 0),
                                            "corrections": ai_result.get("corrections", 0)}
            except Exception:
                warnings.append("Optional text cleanup could not complete within the job time limit.")
            ai_charge = budget.charge
            outcome["ai_charge_microusd"] = ai_charge
            report.setdefault("ai_cleanup", {"enabled": True, "calls": 0, "corrections": 0})
            report["ai_cleanup"]["charge_microusd"] = ai_charge
        elif job["ai_enabled"]:
            warnings.append("Optional text cleanup was requested but is not configured.")
        if warnings:
            report["warnings"] = warnings
        report["ai_charge_microusd"] = ai_charge
        output_path = f"jobs/{job_id}/book.epub"
        preview = _preview(output)
        upload_timeout = min(job["max_seconds"], MAX_WORKER_SECONDS) - (time.monotonic() - started) - 10
        if upload_timeout <= 0:
            raise TimeoutError("job deadline reached")
        body = await asyncio.to_thread(output.read_bytes)
        await asyncio.wait_for(blob.put_blob(output_path, body, access="private",
                                             content_type="application/epub+zip", add_random_suffix=False,
                                             allow_overwrite=False), timeout=upload_timeout)
        outcome.update(status="ready", output_path=output_path, report=report,
                       preview=preview, ai_charge_microusd=ai_charge, error=None)
    except TimeoutError:
        outcome["error"] = "processing_time_limit"
    except ConversionFailure as exc:
        outcome["error"] = str(exc)
    except ValueError as exc:
        outcome["error"] = "invalid_pdf" if str(exc) in {"input is not a PDF", "empty input", "input size rejected"} else "conversion_failed"
    except Exception:
        outcome["error"] = "worker_error"
    finally:
        if blob is not None:
            try:
                await asyncio.wait_for(_delete_blob(blob, input_path), timeout=5)
            except Exception:
                pass
        if temp_dir is not None:
            temp_dir.cleanup()
    outcome["processing_seconds"] = max(0, int(time.monotonic() - started + 0.999))
    return outcome


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/internal/convert")
async def convert_job(request: FastAPIRequest, authorization: str | None = Header(default=None)):
    try:
        gateway, secret = _config()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="worker not configured") from None
    if not _authorized(authorization, secret):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2048:
                break
        payload = json.loads(body) if len(body) <= 2048 else None
        job_id = payload.get("job_id") if isinstance(payload, dict) else None
    except Exception:
        job_id = None
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="invalid job id")
    started = time.monotonic()
    try:
        claim = await asyncio.to_thread(_post_json, gateway + "/api/internal/claim", secret, {"job_id": job_id})
    except RuntimeError:
        raise HTTPException(status_code=503, detail="gateway unavailable") from None
    if claim.get("claimed") is False:
        return JSONResponse({"claimed": False}, status_code=200)
    token = claim.get("claim_token")
    if not isinstance(token, str) or not token or len(token) > 512:
        raise HTTPException(status_code=503, detail="invalid claim response")
    try:
        job = _valid_job(claim.get("job"), job_id)
    except ValueError:
        finish = {"job_id": job_id, "claim_token": token, "status": "failed", "output_path": None,
                  "report": None, "preview": [], "ai_charge_microusd": 0, "processing_seconds": 0,
                  "error": "invalid_job_record"}
    else:
        finish = await _process(job_id, job, token, gateway, secret, started)
        finish["claim_token"] = token
        finish["job_id"] = job_id
    try:
        await asyncio.to_thread(_post_json, gateway + "/api/internal/finish", secret, finish, 10.0)
    except RuntimeError:
        # Queue retries the callback; claim/finish are idempotent gateway operations.
        raise HTTPException(status_code=503, detail="gateway finish unavailable") from None
    return {"claimed": True, "status": finish["status"]}
