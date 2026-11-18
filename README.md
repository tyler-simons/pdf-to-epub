# Rebind

**Your PDFs, made for reading.** Convert PDFs into EPUBs for Apple Books, Kindle via Send to Kindle, and other EPUB readers.

[Try Rebind](https://pdf.tylersimons.com/) · [Send to Kindle](https://www.amazon.com/sendtokindle) · [Deployment guide](docs/vercel.md)

Rebind is a small, self-hostable web app. Digital PDFs become reflowable text; scans use local Tesseract OCR. An optional, budgeted AI pass fixes a small selection of obvious recognition errors. No AI key is needed to run the local converter.

## What it does

- Reflowable EPUBs with reader-controlled type size and themes.
- Bookmark navigation, emphasis, embedded images, and a text preview.
- Page-image fallback for complex tables and equations.
- Private downloads, 24-hour expiry, and explicit deletion.
- Public demo protected by Turnstile, admission limits, processing limits, and a durable AI allowance.

The hosted demo is limited, shared, and offered without an uptime guarantee. It accepts PDFs up to 40 MB and 1,500 pages; processing also has a time limit. Large scans may need smaller sections. Please upload only documents you have permission to process.

## Quick start

Use Python 3.12+. Install Tesseract with English language data for scanned PDFs (`brew install tesseract` on macOS, or `apt install tesseract-ocr tesseract-ocr-eng` on Debian).

```sh
git clone https://github.com/tyler-simons/pdf-to-epub.git
cd pdf-to-epub
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

Open http://localhost:8080. Conversion is local and AI is off by default. Runtime data lives in the ignored `data/` directory. Local conversions share a default three-job daily allowance; set `LEAF_PER_CLIENT_DAILY_JOBS=20` for larger local experiments.

Or run `docker compose up --build`. The Compose configuration binds to localhost and persists data in a volume.

## Deploy on Vercel

The hosted path uses two projects from this repository:

| Component | Location | Purpose |
| --- | --- | --- |
| Gateway | `vercel/` | Interface, private upload authorization, job status, queue callback, downloads |
| Converter | Root `Dockerfile.vercel` | Python, PyMuPDF, Tesseract, isolated conversion subprocess |
| Storage | Private Vercel Blob + Upstash Redis | Files, durable job state, admission and spending reservations |

Follow [docs/vercel.md](docs/vercel.md) for environment variables and setup. Cloning this repository does not provision services, buy credits, or give access to the demo's keys. Each deployment needs its own storage, secrets, and provider account. Never use local SQLite as shared serverless state.

## Optional AI

Set `OPENAI_API_KEY` on the converter and enable `LEAF_AI_ENABLED` on the gateway only after configuring a durable budget. The adapter currently calls OpenAI's Responses API with `gpt-6-luna`, at most once per book, for at most eight short plain-text paragraphs. Selected text is sent to OpenAI; the PDF subprocess never receives the key.

This is **light OCR cleanup, not full-book proofreading**. Inline-formatted passages and many spelling issues are outside its detector. Provider errors leave the original EPUB available with a conversion note.

The Vercel gateway defaults to a $9 lifetime allowance and $0.10 per job. Local defaults are documented in `.env.example`; explicitly set your intended budget. The app reserves conservatively before calling AI and retains an allowance for ambiguous outcomes. These are application limits, not provider prepaid wallets or caps on storage, hosting, bandwidth, or unrelated API-key use. Do not delete the ledger to restart the service.

## Quality and privacy

Text extraction, reading order, paragraph reconstruction, OCR, and font encodings vary between PDFs. Complex pages may be preserved as images, which do not reflow or follow dark themes. Forms are converted for reading; interactive form behavior is not retained. Review important documents against the source.

Uploaded PDFs are removed after conversion. EPUB access expires after 24 hours; deletion and storage TTLs provide cleanup. Budget and admission metadata can remain to enforce limits. Preview text is rendered as text, never executed as document-provided instructions or HTML.

## Development

```sh
pip install -r requirements-dev.txt
python -m pytest tests -q
cd vercel
npm ci
node --test ../tests/node/*.test.mjs
npm run build
```

Node tests execute Redis Lua through a local fakeredis/Lua bridge. Tests generate their own PDF fixtures; user books and credentials are not bundled. A GitHub Actions workflow template is included in `docs/ci/` for maintainers to enable.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines and [docs/verification.md](docs/verification.md) for the scope of verification.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md). PyMuPDF is also AGPL-licensed or available under a separate commercial license. Hosted modifications must meet the applicable source-sharing requirements.

If Rebind is useful to you, a GitHub star helps others find it. Bug reports with a minimal, shareable example are especially welcome.
