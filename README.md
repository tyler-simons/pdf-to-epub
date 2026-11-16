# Leaf

Drop in a PDF. Take away a readable EPUB.

**Vercel deployment path:** see [docs/vercel.md](docs/vercel.md). The Vercel gateway builds locally; hosted services and real API usage still require configuration and smoke tests.

A small, self-hostable pilot for Apple Books, Kobo, Kindle via Send to Kindle, and other EPUB readers. No accounts or payments. Local conversion works without an API key; optional AI cleanup has a durable spending allowance.

## Run locally

Python 3.12+ is required. For scanned pages, install Tesseract with English language data (`brew install tesseract` on macOS; `apt install tesseract-ocr tesseract-ocr-eng` on Debian).

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

Open http://localhost:8080. The default is a local-only pilot, without accounts or AI calls. State is stored in `./data`; keep that folder private and out of source control. Local development shares one three-jobs-per-day allowance. To run a larger local test, set `LEAF_PER_CLIENT_DAILY_JOBS=20` before starting.

## Docker

```sh
docker compose up --build
```

Open http://localhost:8080. Compose binds only to localhost, runs without root, drops Linux capabilities, limits CPU/memory/processes, and persists files and the budget ledger in a named volume. The PDF subprocess also has wall-clock, address-space, CPU, file-size, and open-file limits on Linux. These are process/container limits, not a complete hostile-code sandbox; use a dedicated isolated host for a public pilot.

## Public access

Anyone with the hosted link can convert a PDF. No account or invite code is needed. Public uploads require a server-validated Cloudflare Turnstile check; public mode refuses to start until its keys, hostname and a stable rate-limit secret are configured.

Create a **local**, ignored `.env` for Compose (these are placeholders, not configured accounts):

```dotenv
LEAF_PUBLIC=true
TURNSTILE_SITE_KEY=your-public-site-key
TURNSTILE_SECRET_KEY=your-server-secret
LEAF_PUBLIC_HOSTNAME=pdf.your-domain.example
LEAF_ACCESS_SECRET=a-stable-random-secret-at-least-32-characters-long
LEAF_AI_ENABLED=false
```

Generate the access secret once with `python -c 'import secrets; print(secrets.token_hex(32))'`. Keep it stable across deploys. Create the Turnstile widget for your actual hostname, with action `convert` configured by the client. Tokens are checked on the server for success, hostname and action, and locally marked used to reject replay. A client-side checkbox alone cannot authorize uploads.

The pilot allows three accepted conversions per connection per UTC day, 12 upload attempts per rolling hour, and 20 accepted jobs globally per UTC day. Invalid submissions count toward attempt limits. IPv6 clients are grouped by /64; shared networks can share a limit, so this is a connection limit rather than a verified person limit. Client addresses are HMAC-hashed before storage. Changing browser storage does not reset the server allowance. The global compute and AI caps remain in force even if someone changes addresses.

Deploy behind HTTPS with proxy upload/concurrency limits. Configure your proxy so `request.client` reflects the verified remote address; do not blindly trust arbitrary forwarded headers. Keep one ASGI worker and one app instance with a persistent data volume. `uvicorn` does not load `.env` by itself; export variables or use the host's secret settings. Never commit real keys, uploads, books or the database.

## Optional AI and the $10 allowance

Set `LEAF_AI_ENABLED=true` and configure `OPENAI_API_KEY` as a server secret. This explicitly authorizes sending **selected suspicious text excerpts** to OpenAI. The PDF parser never receives that key. Ordinary local conversion remains available when AI is disabled or the allowance runs out.

The optional adapter uses `gpt-6-luna`, one request maximum per book, at most eight short plain-text paragraphs, and 1,200 maximum output tokens. It detects a limited set of broken-character patterns; it is **not full-book proofreading or a guarantee that all spelling errors are fixed**. Exact, bounded corrections only are applied to unambiguous plain-text slots. Inline-formatted passages are left alone.

The ledger defaults to a **$10 lifetime allowance**, and a **$0.10 allowance per job**. Reservations happen transactionally before any API call. Concurrent work cannot reserve the same money. Provider timeouts and unknown usage retain their full reservation; restarting the app does not refill the allowance. A changed configuration may lower the stored lifetime cap, but cannot raise it. Do not delete or replace the persistent database to restart the service: doing so also discards spend history.

Accounting deliberately charges conservative safety allowances of $1 per million input tokens and $2 per million output tokens. These are **not advertised provider prices**. Confirm that the provider's current model rates remain below these allowances before enabling AI. Input is reserved using serialized UTF-8 byte count plus overhead; output is bounded at the request. After known usage, the unused reserve is released. The app is not connected to a prepaid balance or an automatic top-up. Apply provider-side limits too; unrelated use of the same key is outside this ledger.

No real API calls were needed to develop the local pilot. AI behavior is tested with simulated provider responses.

| Environment setting | Default |
|---|---:|
| `LEAF_AI_BUDGET_MICROUSD` | `10000000` ($10 lifetime) |
| `LEAF_AI_PER_JOB_MICROUSD` | `100000` ($0.10) |
| `LEAF_DAILY_JOBS` | `20` |
| `LEAF_PER_CLIENT_DAILY_JOBS` | `3` |
| `LEAF_DAILY_PROCESSING_SECONDS` | `3600` |
| `LEAF_JOB_TIMEOUT_SECONDS` | `300` |

Compute time is reserved before work and settled afterward. Failed jobs still count toward admission limits. These caps limit conversion workload; they do **not** cap a hosting provider's entire invoice, bandwidth bill, or idle-server charges. Set host billing alerts and service instance limits separately.

## How conversion works

1. An authenticated upload becomes a private job with an unguessable bearer token.
2. A durable SQLite queue dispatches jobs sequentially to a separate converter process.
3. Digital text and embedded images are reconstructed into reflowable XHTML, with bookmark navigation and reader-controlled colors.
4. Scanned pages use local Tesseract. Likely mathematical/table layouts can fall back to page images rather than lose content.
5. Optional bounded AI checks a small set of suspicious excerpts.
6. The app validates the EPUB, shows a text preview and quality warnings, and offers a private download.

Uploads are removed after conversion. EPUBs and reports expire 24 hours after upload, with periodic cleanup while the service is running and cleanup on the next start. Expired links stop working immediately even while a cleanup pass is pending. An explicit delete action removes completed jobs' files immediately. Some aggregate admission/budget records and hashed token metadata remain in SQLite to enforce limits. The preview uses text nodes; source text is never executed as HTML or instructions.

## Quality limits

This is a working pilot, not an unattended publisher-quality service. Complex layouts may become images, which do not reflow or follow dark themes. OCR may contain mistakes. Embedded OCR layers can still be unreliable. Font-encoding problems are flagged rather than guessed globally. Links and footnotes may need additional reconstruction. The preview is a bounded text excerpt, not a complete EPUB reader; download to check illustrations and the complete book.

Several earlier book conversions required source-specific inspection. This app generalizes the baseline pipeline, but does not claim to reproduce every manual repair made in that research.

## Hosting plan

Nothing is deployed or provisioned by this repository. The first step is to benchmark this container on a small host. A sleeping deployment needs a persistent volume and a platform that keeps an accepted background job alive until completion. Do not put this SQLite queue on ephemeral serverless storage or enable scale-to-zero without lifecycle handling. Multi-instance, truly idle-to-zero hosting needs an external durable queue/object store and worker dispatcher; those adapters are future work.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest tests -q
```

Tests generate tiny PDFs; no copyrighted books or credentials are bundled. They cover conversion, complex-page fallback, archive validation, private downloads, public verification and upload limits, expiry, concurrent budget reservations, and simulated AI failures.

## License

AGPL-3.0-or-later; see LICENSE. PyMuPDF is also distributed under AGPL or a separate commercial license. Keep the corresponding source available when hosting this modified AGPL application for others. See THIRD_PARTY.md for dependency notices.

## Design and first launch

The interface follows [Tyler's design system](https://github.com/tyler-simons/tyler-design-system): Manrope/DM Sans (self-hosted), a light blue canvas, dark ink, restrained blue accents and compact controls. No Streamlit-specific theming is used because this is a standalone web app. See [the first-pilot guide](docs/launch.md) before sharing it publicly.
