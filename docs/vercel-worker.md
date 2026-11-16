# Vercel conversion worker

The worker is a stateless FastAPI container deployed as a second Vercel project from the same repository as the Node gateway. Set the worker project's Root Directory to `epub-app` and let Vercel use `Dockerfile.vercel`. The container listens on `$PORT` (default 80); it has no persistent volume or local database.

The Node gateway remains authoritative for job state, access control, admission, and global AI allowance. It calls `POST /internal/convert` with `{ "job_id": "..." }`. The worker authenticates the callback with `Authorization: Bearer $LEAF_WORKER_SECRET`, asks only the configured `LEAF_GATEWAY_URL` for a claim, and does no work unless the gateway returns a claim token and a valid job record. A duplicate callback that is already claimed or terminal returns HTTP 200 without touching Blob storage. Gateway communication failures return 503 so the gateway can retry.

## Project settings

Create a private Vercel Blob store and connect it to the worker project. Configure:

| Variable | Required | Purpose |
| --- | --- | --- |
| `LEAF_GATEWAY_URL` | Yes | HTTPS origin of the Node gateway, without a path or query. |
| `LEAF_WORKER_SECRET` | Yes | At least 32 characters; shared by the gateway callback and worker-to-gateway calls. |
| `BLOB_STORE_ID` plus Vercel OIDC | Recommended | Vercel injects rotating OIDC credentials for the connected private Blob store. |
| `BLOB_READ_WRITE_TOKEN` | Alternative | Use only when OIDC project connection is unavailable. |
| `OPENAI_API_KEY` | Optional | Enables the existing bounded OCR cleanup only for jobs whose gateway record enables it. |

Do not set `OPENAI_API_KEY` in any converter child process. The gateway must send these trusted claim fields: `id`, `title`, `input_path`, `expires_at`, `max_pages`, `max_seconds`, `ai_enabled`, and `ai_allowance_microusd`, plus the top-level `claim_token`. The worker accepts only the exact input pathname `jobs/{job_id}/source.pdf`; it writes `jobs/{job_id}/book.epub` privately without random suffixes or overwrites. The worker deletes the input blob in a `finally` path; gateway expiry cleanup should still cover unexpected process termination.

After processing, the worker posts `job_id`, `claim_token`, `status`, `output_path`, `report`, `preview`, `ai_charge_microusd`, `processing_seconds`, and a fixed non-sensitive error code to `/api/internal/finish`. The gateway should accept `ready` and `failed` as terminal statuses and treat repeated finish callbacks idempotently. A report's AI charge uses actual provider usage when settled and the full durable reservation when its outcome is unknown.

## Runtime limits

The worker rejects PDFs above 40 MiB, checks the `%PDF-` signature while streaming to `/tmp`, limits pages to 1,000 and one job to at most 240 seconds. It runs the existing converter through `app.runner` with OS resource ceilings and an environment allowlist; the API key is not inherited. Local Tesseract OCR is installed in the image. No database, queue poller, or background task loop runs in the container.

Keep the gateway's queue callback request open until `/internal/convert` returns. Configure its invocation for a maximum of 240 seconds; the container function must have a platform duration of at least 300 seconds to leave time for the finish request. The gateway should provide its own queue retry and idempotency policy. Vercel container functions are stateless, scale to zero, and route HTTP requests to the container port; a local `/tmp` workspace is disposable and never used as durable storage.

## Local tests

Install `requirements-vercel.txt` in an isolated environment, then run:

```sh
python -m pytest tests/test_vercel_worker.py
```

Tests inject fake gateway and Blob implementations. They make no Vercel, Blob, or OpenAI requests. For a local container smoke check, set the two gateway variables and run `docker build -f Dockerfile.vercel -t leaf-vercel-worker .` from `epub-app`, then start it with `docker run -p 8080:80` and check `/health`.
