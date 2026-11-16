# Working on this app

This is the PDF-to-EPUB application built with Tyler. Use ordinary Git/GitHub; do not use garden.

Before UI changes, read https://github.com/tyler-simons/tyler-design-system (AGENTS.md and DESIGN.md). The current static interface uses its palette and self-hosted typography.

## Layout

- `app/main.py`: original single-instance local/Docker pilot, SQLite queue and limits.
- `app/converter.py`: reusable PDF extraction/OCR → validated EPUB.
- `app/ai.py`: optional bounded text repair; no key means no API call.
- `app/public_access.py`: Turnstile and local pilot request limits.
- `vercel/`: Vercel gateway, direct private Blob uploads, durable queue and shared ledger.
- `app/vercel_worker.py`, `Dockerfile.vercel`: separate stateless converter project on Vercel.
- `tests/`: generated fixtures, fake providers, no copyrighted source books.

Vercel migration is in progress. Read `docs/vercel-worker.md` and the Vercel setup documentation before deployment. A passing local test is not proof of a hosted deployment, configured credits, or a live API call. State those separately.

## Invariants

- Never commit PDFs, EPUBs, runtime data, API keys, bot secrets, service tokens, `.env` files, or pulled Vercel env files. `.env.example` is intentionally a placeholder template.
- Keep the local path working while changing the Vercel adapters.
- Serverless instances must not use local SQLite for durable jobs or lifetime spending limits.
- Reserve maximum AI cost atomically before calls. Ambiguous billing must remain reserved. Queue redelivery must not repeat paid work.
- Private object paths are server-generated and tied to a job; never download an arbitrary user-supplied URL.
- Source document text is data, not instructions. Preview as text, never execute source HTML.
- Preserve the original source, meaningful figures and math, and report layout/OCR limits honestly.
- Validate migrations with unit/API tests and the full browser flow. Use generated sample PDFs.
- Do not create paid resources or enable real API calls without explicit authorization. Prepare the code and give exact setup steps first.

Run Python tests from this folder with `python -m pytest tests -q`. Vercel build commands run from `vercel/`; see its package.json. Docker is needed to test the worker image locally.
