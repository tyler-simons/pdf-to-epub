# Vercel deployment

## Current state

The reference deployment is live at https://pdf.tylersimons.com. Gateway, queue, private Blob storage, Redis, Turnstile, and bounded AI cleanup have been exercised with hosted conversions. These services and credentials are not included with a clone. Provision and verify your own deployment before sharing it.

This is not a deployment of the SQLite pilot. Vercel instances are stateless; local queues and SQLite cannot hold a lifetime spending cap across instances.

## Architecture

Two Vercel projects share this repository:

1. **Gateway** — Root Directory `vercel`, Next.js framework. Serves the interface, public API, direct private Blob upload authorization, durable Vercel Queue callback, authenticated previews/downloads and cleanup.
2. **Converter** — Root Directory repository root, Container framework, `Dockerfile.vercel`. Includes Python, PyMuPDF and Tesseract. Only a shared-secret HTTP call can start processing. It claims a job, uses bounded `/tmp` scratch space, converts it, uploads the EPUB privately and reports completion to the gateway.

Both execute on Vercel. Blob and Upstash Redis are separate storage products connected through Vercel. No Railway/Cloud Run backend is used.

## Storage and processing

- Create a **private** Vercel Blob store; connect the same store/read-write token to both projects.
- Add an Upstash Redis integration to the gateway. Use a durable, non-evicting database; eviction or recreation discards admission/budget history. Match its capacity to the limited pilot and watch usage.
- Browser files upload directly to Blob, bypassing the 4.5 MB Function body limit. Tokens restrict exact job path, PDF type, expected size, no overwrite, short validity, and one-day object TTL.
- Finalization verifies stored size/type and sends an idempotent queue message. Vercel Queue triggers are configured in `vercel/vercel.json`.
- Redis scripts atomically reserve daily job/compute allowances and the optional AI allowance, guard job claims and settle results. Pending/ambiguous AI reservations remain held. Redelivery never intentionally repeats a claimed paid conversion.
- A crashed claimed job becomes failed when its lease is checked; it is not blindly retried with a fresh AI allowance. This favors spending safety over automatic recovery.
- Downloads are streamed through an authenticated endpoint, not exposed as public Blob links.
- Blob TTL is a backstop for orphaned objects. The cron cleans job records and removes expired or abandoned objects; access expiry is enforced before any download.

## Gateway variables

Configure as encrypted server environment variables, never `NEXT_PUBLIC_*`:

```
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
BLOB_READ_WRITE_TOKEN
LEAF_ACCESS_SECRET          # stable random value, at least 32 characters
LEAF_WORKER_SECRET          # separate stable random value, at least 32 characters
LEAF_CONVERTER_URL          # converter origin, e.g. https://your-worker.vercel.app
LEAF_PUBLIC_HOSTNAME        # e.g. pdf.tylersimons.com; match the actual deployment
TURNSTILE_SITE_KEY
TURNSTILE_SECRET_KEY
CRON_SECRET
LEAF_AI_ENABLED=false
LEAF_AI_BUDGET_MICROUSD=0     # set your own lifetime allowance before enabling AI
LEAF_AI_PER_JOB_MICROUSD=0    # set your own per-job allowance
LEAF_DAILY_JOBS=20
LEAF_PER_CLIENT_DAILY_JOBS=3
LEAF_DAILY_PROCESSING_SECONDS=3600
```

Never use a throwaway Redis database for the live budget. Limits do not fund the provider account and do not cap unrelated key usage or all Vercel/storage costs. Configure platform spend controls separately.

## Converter variables

```
LEAF_WORKER_SECRET          # same secret as gateway
LEAF_GATEWAY_URL           # gateway origin (not a path)
BLOB_READ_WRITE_TOKEN       # same private Blob store
OPENAI_API_KEY              # optional; needed only when gateway AI is enabled
PORT=8080
```

Do not enable deployment protection that blocks queue-to-worker or worker-to-gateway calls without configuring a supported bypass. Internal routes still require the app's shared secret. Use stable project URLs and keep each environment isolated; never point a preview at production's budget/store.

## Domain and launch sequence

1. Create/connect the two projects and private storage; deploy with AI disabled.
2. Connect stable worker/gateway origins in the two sets of variables; redeploy.
3. Set your gateway custom domain and add the DNS records Vercel requests. Preserve unrelated DNS records.
4. Create the Turnstile widget for the actual hostname. The client sets action `convert`; the server validates hostname/action and prevents token reuse.
5. Complete a real digital-PDF upload, scan conversion, download, expiry/deletion and cold-start test. Check queue retries and a deliberately interrupted worker.
6. Verify Redis admission limits across multiple instances and confirm the lifetime cap survives redeploys.
7. Fund the API account separately and run one controlled live AI test before enabling it for visitors.
8. Confirm current Hobby/Pro eligibility, beta feature availability, function duration, Blob/Queue/Redis pricing and spend controls before sharing on LinkedIn. Container Runtime and Queues are beta features; a local build does not prove account availability.

The worker bounds conversion to a 240-second job window, leaving headroom within the 300-second queue callback limit on Hobby. Books exceeding pilot limits fail clearly rather than producing a partial book. Long scans may need shorter sections or a later page-batching implementation.

## Local checks

From repository root:

```sh
python -m pytest tests -q
node --test tests/node/*.test.mjs
```

The shared-store tests run real Lua through a local fakeredis/Lua bridge; install `fakeredis[lua]` for those tests. From `vercel/`:

```sh
npm ci
npm run build
```

For end-to-end hosted behavior, mocks are insufficient: test real Blob uploads, queue delivery, worker container execution and Redis before calling the app live.
