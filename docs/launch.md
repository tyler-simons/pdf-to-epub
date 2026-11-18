# Launching your own deployment

The reference app is hosted, but a new clone needs its own services, credentials and verification. See [the Vercel guide](vercel.md) for the gateway/worker architecture. The local SQLite pilot requires a single instance and persistent storage; it is not shared serverless state.

1. Provision private storage, a durable job/budget ledger, queue delivery and an isolated converter. Verify processing and resource limits in the actual hosting environment.
2. Use HTTPS and configure platform billing alerts, upload limits and concurrency limits. Application compute caps do not bound the entire hosting invoice.
3. For optional AI, create a dedicated provider project/key and choose your own provider funding and recharge policy. Store the key only as a server secret. API keys do not have individual prepaid wallets.
4. Configure explicit lifetime and per-job application allowances, then enable AI only after a controlled test. The UI discloses that selected text may be sent to OpenAI. This is limited cleanup, not full proofreading.
5. Verify a real provider response and ledger settlement. Timeouts and ambiguous outcomes retain conservative reservations. Provider billing cutoff can be delayed; provider controls and the application ledger serve different purposes. Unrelated key usage is outside this ledger.
6. Configure Turnstile for your hostname and generate stable access/worker secrets. Verify server-side hostname/action validation and replay rejection. Public uploads should not depend on a client-side checkbox alone.
7. Test ordinary, scanned, long, locked and malformed PDFs; downloads, preview, deletion, expiry and queue redelivery. Inspect important EPUBs in real reader apps.
8. Do not delete or replace the durable ledger when restarting the service: it contains job state and lifetime allowance history. Do not commit keys, uploaded documents, runtime databases or private download links.

See [verification scope](verification.md) and [corpus results](corpus-results.md) for observed behavior and limitations. Tests generate fixtures or download publicly available examples into an ignored local cache; private books are not distributed.
