# Public PDF compatibility corpus

Measured locally on 2026-11-17 with PyMuPDF and installed Tesseract; no AI or hosted conversion calls. Thirteen public PDFs, 84 pages total: all thirteen produced internally validated EPUBs with one XHTML page per source page. This is a **structural pass**, not a fidelity certification. All sources have SHA256 pins in [the manifest](../tests/corpus/manifest.json). Downloaded PDFs and generated EPUBs remain in ignored `.corpus-cache/`; none are bundled.

## Measurements

| Source | Category | Pages | Raster fallback | Searchable token retention | Warnings |
| --- | --- | ---: | ---: | ---: | ---: |
| [Two columns (Word)](https://www.w3.org/WAI/WCAG21/working-examples/pdf-reading-order/reading-order-2cols-word.pdf) | columns | 1 | 0/1 | 100.0% | 0 |
| [Two columns (OpenOffice)](https://www.w3.org/WAI/WCAG21/working-examples/pdf-reading-order/reading-order-2cols-oo.pdf) | columns | 1 | 0/1 | 100.0% | 0 |
| [Tab and reading order](https://www.w3.org/WAI/WCAG21/working-examples/pdf-reading-order/reading-order.pdf) | form | 1 | 0/1 | 100.0% | 0 |
| [OCR recipe](https://www.w3.org/WAI/WCAG21/working-examples/pdf-ocr/ocr-example.pdf) | ocr-text-layer | 1 | 0/1 | 100.0% | 0 |
| [Tagged OCR recipe](https://www.w3.org/WAI/WCAG21/working-examples/pdf-ocr/ocr-example-tagged.pdf) | ocr-text-layer | 1 | 0/1 | 100.0% | 0 |
| [Accessible form](https://www.w3.org/WAI/WCAG21/working-examples/pdf-name-role-value-form-fields/form.pdf) | form | 1 | 0/1 | 100.0% | 0 |
| [IRS W-4 English](https://www.irs.gov/pub/irs-pdf/fw4.pdf) | multilingual-form | 5 | 2/5 | 55.1% | 2 |
| [IRS W-4 Spanish](https://www.irs.gov/pub/irs-pdf/fw4sp.pdf) | multilingual-form | 5 | 2/5 | 57.6% | 2 |
| [IRS W-4 Korean](https://www.irs.gov/pub/irs-pdf/fw4ko.pdf) | multilingual-form | 5 | 2/5 | 51.1% | 2 |
| [IRS W-4 Russian](https://www.irs.gov/pub/irs-pdf/fw4ru.pdf) | multilingual-form | 5 | 2/5 | 54.6% | 2 |
| [Attention Is All You Need v7](https://arxiv.org/pdf/1706.03762v7) | math-tables-two-columns | 15 | 6/15 | 50.7% | 6 |
| [Operation LION Apollo 12 report](https://ntrs.nasa.gov/api/citations/19700020471/downloads/19700020471.pdf) | historic-report | 14 | 5/14 | 70.0% | 5 |
| [Spacecraft thermal control report](https://ntrs.nasa.gov/api/citations/19660015096/downloads/19660015096.pdf) | historic-report | 29 | 0/29 | 100.0% | 0 |

Raster fallback counts full-page image preservation, excluding embedded illustrations. Token retention is the overlap of normalized word multisets between PDF extraction and EPUB paragraphs; it does not measure reading order. Nineteen pages were preserved as raster fallbacks and nineteen warnings reported this limitation. All thirteen reports had `full_text_missing=false`; this field therefore does **not** mean that all text remains searchable: the IRS/math fallback pages have no reflowable transcription.

## Fidelity findings

- Both W3C two-column examples initially failed expected left-column-before-right order. `Nullam a est eget ipsum` must precede `2. Nulla malesuada`; global vertical block sorting interleaved the columns. After the column-order repair, both expected-sequence probes pass and both retain all extracted tokens. This illustrates why token counts alone are insufficient. Final measurements are in [results.json](../tests/corpus/results.json). Baseline measurements are preserved in [baseline-results.json](../tests/corpus/baseline-results.json).
- After repair, first/last six-token presence probes passed both ends for nine PDFs. The OpenOffice example’s opening contiguous anchor now passes. All four IRS forms failed both anchors because their first/last pages became image fallbacks.
- W3C OCR examples and both NASA documents already contain text layers. Their successful conversion does not establish fresh OCR accuracy. A separate generated raster-only page exercised Tesseract and produced searchable text (`ocr_pages=1`, no missing-text warning), including the expected lighthouse sentence.
- Interactive form widgets were detected (3 and 7 in W3C samples; 48 per IRS form), but EPUB form interactivity/state is not preserved or verified. Tables and mathematical notation remain image fallbacks where detected. No claim of accessible semantic tables or MathML is made.

## Generated rejection probes

Local generated probes passed: password-encrypted PDF → `password_required`; 1,501-page PDF → `page_limit`; malformed PDF → `conversion_failed`. Each failed cleanly and produced no EPUB. Raster-only English scan succeeded. Generated probes are reproducible from [run_corpus_fixtures.py](../scripts/run_corpus_fixtures.py), not binary fixtures.

## Reproduce

See [corpus instructions](../tests/corpus/README.md). Run `python scripts/run_corpus.py` for downloads plus local conversions, or `--offline` after caching. Bounds: 40 MiB input; 1,500 pages; 240 seconds per converter process; 40-second network-read timeout. `--only` selects cases. The committed baseline and final results store measurements and hashes, not source document text.

Discovery used W3C’s [PDF3](https://www.w3.org/WAI/WCAG21/Techniques/pdf/PDF3), [PDF7](https://www.w3.org/WAI/WCAG21/Techniques/pdf/PDF7), [PDF12](https://www.w3.org/WAI/WCAG21/Techniques/pdf/PDF12), the [IRS public PDF directory](https://www.irs.gov/downloads/irs-pdf), [arXiv version record](https://arxiv.org/abs/1706.03762v7), and NASA STI records ([Apollo](https://ntrs.nasa.gov/citations/19700020471), [thermal control](https://ntrs.nasa.gov/citations/19660015096)). Byte downloads verified that every manifest URL returned an actual PDF.

This is a small compatibility corpus, not a universal quality score. It lacks independently transcribed ground truth, difficult non-English raster OCR, handwriting, damaged scans and comprehensive reader-device visual/accessibility testing. First/last anchors can survive scrambled interior content; a retained extracted text layer can itself contain OCR errors.

A separate user-provided 1,078-page textbook previously completed hosted conversion. It is outside this public corpus; its PDF, generated book and private filename are not distributed.

Final rerun uses `app.runner` with an allowlist of PATH, PYTHONPATH, LANG, LC_ALL, OMP_THREAD_LIMIT and LEAF_CHILD_SECONDS; credentials are not inherited. Linux adds memory/CPU/file ceilings. Conversion timeouts terminate the whole process group, including OCR. All thirteen sources and four generated probes were rerun through this path.
