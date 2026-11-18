# First shared pilot

The local app is functional. Hosted operation and real API access have not been verified yet.

1. Choose a host with one app instance and persistent storage. Test the Docker image and its Linux resource limits there. The current SQLite worker is not safe to put on ephemeral storage or arbitrary scale-to-zero hosting.
2. Use HTTPS. Configure proxy upload/concurrency limits, host billing alerts and one maximum instance. App compute caps do not bound an entire hosting bill.
3. Create a dedicated API project/key; fund the provider account with the intended $10 and disable automatic recharges if desired. Keep the key in server secrets, not source control or frontend code. The API key itself is not a prepaid voucher.
4. Set `LEAF_AI_ENABLED=true` and `OPENAI_API_KEY`. The UI then discloses that selected text may be sent to OpenAI. Only a small set of suspicious excerpts is sent, not every paragraph.
5. Keep the app allowance separate. For a small buffer below the funded balance, set `LEAF_AI_BUDGET_MICROUSD=9000000` ($9) and leave `LEAF_AI_PER_JOB_MICROUSD=100000` ($0.10). The persistent ledger conservatively reserves before calls and holds unknown charges. Confirm current provider rates remain below its safety allowances. Other uses of that key are outside this ledger.
6. Do one controlled live conversion containing a suspicious OCR token. Verify a real completed response, provider usage, the app ledger, output readability and the result when funds are unavailable. The reference deployment has passed a controlled live AI test; repeat it for your own deployment.
7. Configure open access: create a Turnstile widget for the hosted hostname, add the public site key and private secret key, set `LEAF_PUBLIC_HOSTNAME`, generate a stable `LEAF_ACCESS_SECRET`, then set `LEAF_PUBLIC=true`. Anyone with the link can use it. Verify real challenges and failed/replayed tokens on the hosted domain. Default limits are three conversions per connection per UTC day, 12 upload attempts per rolling hour and 20 jobs globally per UTC day. Shared networks can share a connection allowance. No invite codes are used.
8. Verify downloaded books in real reader apps, and exercise failed/scanned/large PDFs, deletion and expiry before announcing the beta.

A $10 provider balance is not an instantaneous usage cutoff; delayed charges are possible. The app ledger adds its own admission control. Do not remove the persistent database between deploys, because it contains both jobs and lifetime allowance history.

Provider reference: https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing
Design reference: https://github.com/tyler-simons/tyler-design-system/blob/main/DESIGN.md
