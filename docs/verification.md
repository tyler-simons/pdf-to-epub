# Pilot verification

Local verification on November 15, 2026:

- Generated PDF → authenticated upload → background conversion → preview → EPUB download → explicit deletion worked through Chrome.
- Desktop and 390px-wide mobile layouts rendered without horizontal overflow; no browser JavaScript errors.
- Preview font-size and night-theme controls exercised.
- Automated tests cover generated digital/scanned PDFs, tables and page rules, archive/XML output, private tokens, upload caps, expiry/deletion, worker timeout, durable concurrent budgets, and bounded AI response handling.
- Real local Tesseract OCR exercised when available.
- No real AI API calls or charges, no hosted deployment, no GitHub publication, and no keys or third-party books in the source package.
- Docker configuration is supplied but was not built locally because Docker is not installed. Linux-only resource ceilings remain to be checked in the container before public deployment.

Screenshots: [upload](upload.png), [reader](reader.png), [mobile](mobile.png).

Design update: read Tyler’s design-system README, AGENTS.md and DESIGN.md before redesigning. Applied self-hosted Manrope/DM Sans, canvas/ink/sky tokens, restrained surfaces and compact type. Darkened muted text, input boundaries and coral focus treatment to meet contrast goals; the ebook preview retains serif reading type. The private interactive guide could not be fetched, so repository instructions were used. Re-ran the browser flow and all 28 automated tests; checked conditional AI disclosure with a simulated config response. Real API usage remains untested.

Public-access update: removed invite codes, added server-validated Turnstile, a local challenge replay guard, secret-keyed connection identities and atomic rolling-hour attempt limits. Unit/API tests use simulated Cloudflare responses; the frontend is checked with a simulated widget. No Cloudflare account or production widget is configured, and no hosted challenge has been verified.

Public pilot verification: 36 automated tests pass. Browser simulation confirms the challenge gates submission, the token is forwarded, the full conversion/preview/deletion flow works and tokens are cleared after use. No live Cloudflare or OpenAI requests were made.

GitHub CI template is supplied at `docs/ci/github-actions-test.yml`. It is not installed as a workflow because the current GitHub credential lacks workflow-write permission. Local test/build results are the current evidence.
