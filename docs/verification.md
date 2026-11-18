# Verification scope

Rebind's reference deployment is https://pdf.tylersimons.com. This file describes observed checks, not a guarantee for every PDF or for a fresh self-hosted deployment.

## Automated checks

The local suite currently has 49 Python tests and 15 Node tests. It covers generated digital/scanned PDFs, equations and tables, archive/XML/reference validation, authenticated private downloads, expiry, admission limits, concurrent durable budget reservations, Redis Lua state transitions, worker claim/idempotency, subprocess timeouts, safe error propagation, and simulated AI outcomes. See README.md for commands.

The Vercel gateway production build passes. A workflow template is provided in docs/ci; it is not currently an active GitHub Actions workflow.

## Hosted checks

- Real private Blob upload, Vercel Queue delivery, Python container conversion, Redis state transitions, preview and EPUB download completed.
- A controlled live AI test corrected three planted OCR errors after provider funding was configured. Paid services are mocked in the automated suite.
- Three public-corpus sources completed hosted conversion after the reading-order fix; both column examples passed the expected preview order, with AI disabled.
- A 1,078-page textbook converted and the complete roughly 80 MB EPUB downloaded and passed structural validation. The source book is not distributed in this repository.
- The default laptop view was checked at a 1280 × 650 CSS-pixel viewport, including real Turnstile rendering. Expanded content and errors can intentionally extend the page.
- The layout and native disclosure controls were inspected at desktop and mobile widths. Full-book typography, reading order, and editorial correctness are not established by a successful build or archive validation.

## External corpus

See [corpus-results.md](corpus-results.md) for the reproducible public-document corpus and its measured results. Network sources can change; cache hashes identify the exact downloaded files. Structural success is distinct from text quality, reflow, reading order, and faithful equation/table layout.

## Limits

OCR can contain errors. Unreliable PDF font encodings, multi-column reading order, footnotes, and complex layouts may need review or a source-specific repair. The optional AI pass checks only a few suspicious plain-text paragraphs. Large scans may exceed the hosted processing window; the app should fail with a useful message rather than return a partial book.
