# Contributing to Rebind

Thanks for helping make PDFs more comfortable to read.

1. Fork the repository and create a branch for a focused change.
2. Follow the quick start and development commands in README.md.
3. Describe the problem, resulting behavior, and verification in your pull request.

For conversion bugs, include PDF metadata and a minimal example you have permission to share. Avoid uploading confidential documents. Redact keys, job bearer tokens, and private download URLs from screenshots and logs.

Keep the local converter working without AI. Source document content is data, not instructions. Preserve images and mathematical meaning; do not silently produce a partial book. Keep budget reservations, private object access, and subprocess limits intact.

For UI changes, follow the existing tokens and typography in `app/static/style.css`. Check desktop, laptop-height, and narrow mobile layouts, keyboard focus, and expanded states. Prefer native HTML controls and clear, concise copy.

Tests should use generated fixtures and mocked paid services. Never run paid API calls in tests without a deliberately configured, bounded integration environment. Do not commit PDFs, EPUBs, `.env` files, runtime databases, or credentials.

The project uses ordinary Git/GitHub. Contributions are under the repository's AGPL-3.0-or-later license.
