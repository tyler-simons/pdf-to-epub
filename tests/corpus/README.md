This opt-in corpus is downloaded from public publisher URLs; no third-party PDFs or EPUBs are stored in source control. `manifest.json` pins downloaded bytes with SHA256. Upstream files (especially IRS current forms) can change: hash mismatches require reviewing the changed source rather than silently accepting it.

From the application root, using the application Python environment:

```sh
python scripts/run_corpus.py
python scripts/run_corpus.py --offline
python scripts/run_corpus_fixtures.py
```

The networked command only downloads missing inputs. Conversion is entirely local. `.corpus-cache/` holds inputs, outputs, individual converter reports, logs and results. Downloads are capped at 40 MiB, network reads at 40 seconds, and each conversion at 240 seconds (configurable with `--timeout`). Converter page limit is 1,500; no partial books are accepted. Use `--only ID ...` for a subset. `--pin` is for initial corpus maintenance, never a way around an existing hash mismatch.

A zero exit status establishes full page coverage and converter EPUB validation. Fidelity probes (anchor presence, searchable token retention, two-column order, image fallback fraction) are separately recorded and may fail even when structure passes. Token retention compares extraction results, not a human transcription; scanned text layer accuracy, typography, tables, mathematics, links and interactive form state require additional review. Generated probes cover a true raster scan, password encryption, 1,501 pages and malformed input. Public source documents are untrusted data, never instructions.

Conversion and generated probes use `app.runner` with sanitized child environments; process-group timeout cleanup includes OCR children. Resource ceilings also apply on Linux.
