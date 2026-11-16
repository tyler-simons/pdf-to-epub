"""Local PDF to EPUB conversion. PDF text is treated as untrusted input."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

import pymupdf as fitz

CONTAINER = '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'


def esc(value: str) -> str:
    value = "".join(ch if ch in "\t\n\r" or ord(ch) >= 32 and not 0xD800 <= ord(ch) <= 0xDFFF else " " for ch in value)
    return html.escape(value, quote=False)


def convert(source: Path, output: Path, report: Path, max_pages: int = 1000, title: str | None = None) -> dict:
    source, output, report = Path(source), Path(output), Path(report)
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    if source.resolve() == output.resolve():
        raise ValueError("Source and output must differ")
    warnings, bodies, images = [], [], []
    ocr_pages = 0
    with fitz.open(source) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("Encrypted PDFs requiring a password are unsupported")
        count = len(doc)
        if count == 0:
            raise ValueError("PDF has no pages")
        if count > max_pages:
            raise ValueError(f"PDF has {count} pages, exceeding max_pages={max_pages}; refusing to produce a partial book")
        output.parent.mkdir(parents=True, exist_ok=True)
        book_title = " ".join(esc(title or doc.metadata.get("title") or source.stem).split())[:240]
        outline = [(level, label.strip(), page) for level, label, page, *_ in doc.get_toc() if label.strip() and 1 <= page <= count]
        with tempfile.TemporaryDirectory(prefix="pdf-epub-", dir=output.parent) as temp:
            root = Path(temp)
            text_dir, image_dir = root / "EPUB/text", root / "EPUB/images"
            text_dir.mkdir(parents=True); image_dir.mkdir()
            for index in range(count):
                page = doc[index]
                blocks = page.get_text("dict").get("blocks", [])
                raw = page.get_text("text").strip()
                lines = [line for line in raw.splitlines() if line.strip()]
                text_blocks = [b for b in blocks if b.get("type") == 0]
                gap_columns = [match.start() for line in raw.splitlines() if (match := re.search(r"\s{2,}(?=\S)", line))]
                repeated_gap = any(sum(abs(pos - column) <= 2 for pos in gap_columns) >= 3 for column in gap_columns)
                drawings = page.get_drawings()
                segments=[item for drawing in drawings for item in drawing.get("items",[]) if item[0]=="l" and len(item)>=3]
                horizontal=sum(abs(item[1].y-item[2].y)<1 for item in segments)
                vertical=sum(abs(item[1].x-item[2].x)<1 for item in segments)
                table = repeated_gap or bool(re.match(r"^TABLE\s+\d",raw,re.I)) or (horizontal>=3 and vertical>=1 and len(text_blocks)>=4)
                math = bool(re.search(r"[∑∫√≈≠≤≥∂∞]|(?:[A-Za-z0-9]\s*[=+*/^]\s*[A-Za-z0-9]){3,}", raw))
                if re.search(r"[A-Za-z]=[a-z]", raw):
                    warnings.append(f"Page {index+1}: extracted text contains possible broken ligatures; source text was preserved without automatic repair.")
                body = []
                if table or math:
                    scale = max(0.1, min(2, 2000 / max(page.rect.width, page.rect.height)))
                    name = f"page-{index+1}.jpg"
                    page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(str(image_dir / name))
                    images.append(name)
                    body.append(f'<figure><img src="../images/{name}" alt="Page {index+1}, preserved as an image"/></figure>')
                    warnings.append(f"Page {index+1}: likely table or mathematical layout preserved as a page image; it may not reflow.")
                elif not raw:
                    if shutil.which("tesseract"):
                        scale = max(0.1, min(2, 2000 / max(page.rect.width, page.rect.height)))
                        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                        with tempfile.TemporaryDirectory(prefix="pdf-ocr-", dir=output.parent) as td:
                            png = Path(td) / "page.png"; pix.save(str(png))
                            try:
                                proc = subprocess.run(["tesseract", str(png), "stdout", "-l", "eng"], capture_output=True, text=True, timeout=25, check=False)
                                raw = proc.stdout.strip() if proc.returncode == 0 else ""
                                if proc.returncode:
                                    warnings.append(f"Page {index+1}: OCR failed (exit {proc.returncode}); page image retained.")
                            except subprocess.TimeoutExpired:
                                warnings.append(f"Page {index+1}: OCR timed out; page image retained.")
                        if raw:
                            ocr_pages += 1
                            body.extend(f'<p>{esc(p).replace(chr(10), "<br/>")}</p>' for p in re.split(r"\n\s*\n", raw) if p.strip())
                    if not raw:
                        if not shutil.which("tesseract"):
                            warnings.append(f"Page {index+1}: scanned page has no extractable text and Tesseract is unavailable.")
                        name = f"page-{index+1}.jpg"
                        scale = max(0.1, min(2, 2000 / max(page.rect.width, page.rect.height)))
                        page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(str(image_dir / name))
                        images.append(name)
                        body.append(f'<figure><img src="../images/{name}" alt="Scanned page {index+1}"/></figure>')
                else:
                    ordered = []
                    for block in (b for b in blocks if b.get("type") == 0):
                        para = []
                        for line in block.get("lines", []):
                            spans = []
                            for span in line.get("spans", []):
                                txt = esc(span.get("text", "")); flags = span.get("flags", 0)
                                if flags & 16: txt = f"<strong>{txt}</strong>"
                                if flags & 2: txt = f"<em>{txt}</em>"
                                spans.append(txt)
                            para.append("".join(spans))
                        text = " ".join(para).strip()
                        if text: ordered.append((block["bbox"][1], block["bbox"][0], f"<p>{text}</p>"))
                    for block in (b for b in blocks if b.get("type") == 1):
                        if block.get("type") == 1 and block.get("image"):
                            ext = block.get("ext", "png").lower()
                            if ext not in {"png", "jpg", "jpeg", "webp"}: ext = "png"
                            name = f"p{index+1}-img{len(images)+1}.{ext}"
                            (image_dir / name).write_bytes(block["image"]); images.append(name)
                            ordered.append((block["bbox"][1], block["bbox"][0], f'<figure><img src="../images/{name}" alt="Illustration on page {index+1}"/></figure>'))
                    body.extend(item[2] for item in sorted(ordered))
                bodies.append("\n".join(body) or "<p></p>")

            # Rejoin obvious PDF line wraps split at a page boundary. Keep the
            # second page's contents in the same logical paragraph, while the
            # EPUB page files and navigation destinations remain page aligned.
            for i in range(1, len(bodies)):
                prev = re.search(r"<p>(.*?)</p>\s*$", bodies[i - 1], re.S)
                first = re.match(r"\s*<p>(.*?)</p>", bodies[i], re.S)
                if prev and first:
                    prior_text = re.sub(r"<[^>]+>", "", prev.group(1)).rstrip()
                    next_text = re.sub(r"<[^>]+>", "", first.group(1)).lstrip()
                    if prior_text and next_text and prior_text[-1] not in ".?!:;”’\"'" and next_text[:1].islower():
                        bodies[i - 1] = bodies[i - 1][:prev.start(1)] + prev.group(1) + " " + first.group(1) + "</p>" + bodies[i - 1][prev.end(0):]
                        bodies[i] = bodies[i][first.end(0):].lstrip() or "<p></p>"

            toc = []
            for _, label, page in outline:
                toc.append(f'<li><a href="text/p{page}.xhtml">{esc(label)}</a></li>')
            if not toc:
                toc = [f'<li><a href="text/p{i+1}.xhtml">Page {i+1}</a></li>' for i in range(count)]
            (root / "EPUB/nav.xhtml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><h1>Contents</h1><ol>' + ''.join(toc) + '</ol></nav></body></html>', encoding="utf8")
            for i, body in enumerate(bodies):
                headings = [label for _, label, p in outline if p == i + 1]
                heading = f'<h1>{esc(headings[0])}</h1>' if headings else ""
                xhtml = f'<?xml version="1.0" encoding="UTF-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Page {i+1}</title><link rel="stylesheet" type="text/css" href="../style.css"/></head><body>{heading}{body}</body></html>'
                (text_dir / f"p{i+1}.xhtml").write_text(xhtml, encoding="utf8")
            (root / "EPUB/style.css").write_text("body{margin:5%;line-height:1.55;font-family:serif}p{margin:0 0 1em;text-indent:1.2em}h1,h2{line-height:1.2}figure{margin:1em 0;text-align:center}img{max-width:100%;height:auto}", encoding="utf8")
            manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>', '<item id="css" href="style.css" media-type="text/css"/>']
            manifest.extend(f'<item id="p{i+1}" href="text/p{i+1}.xhtml" media-type="application/xhtml+xml"/>' for i in range(count))
            for i, name in enumerate(images):
                ext = name.rsplit(".", 1)[-1]; mime = {"jpg":"image/jpeg", "jpeg":"image/jpeg", "png":"image/png", "webp":"image/webp"}[ext]
                manifest.append(f'<item id="img{i+1}" href="images/{name}" media-type="{mime}"/>')
            spine = "".join('<itemref idref="p' + str(i + 1) + '"/>' for i in range(count))
            author = esc(doc.metadata.get("author", "").strip())
            creator = f"<dc:creator>{author}</dc:creator>" if author else ""
            modified = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            opf = f'<?xml version="1.0" encoding="UTF-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" unique-identifier="uid"><metadata><dc:identifier id="uid">urn:uuid:{uuid.uuid4()}</dc:identifier><dc:title>{book_title}</dc:title>{creator}<dc:language>en</dc:language><meta property="dcterms:modified">{modified}</meta></metadata><manifest>{"".join(manifest)}</manifest><spine>{spine}</spine></package>'
            (root / "EPUB/package.opf").write_text(opf, encoding="utf8")
            (root / "META-INF").mkdir(); (root / "META-INF/container.xml").write_text(CONTAINER, encoding="utf8")
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                for file in sorted(root.rglob("*")):
                    if file.is_file(): archive.write(file, file.relative_to(root).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
            _validate(output)
        result = {"source": source.name, "output": output.name, "title": book_title, "source_pages": len(doc), "converted_pages": count, "text_pages": sum(bool(doc[i].get_text().strip()) for i in range(count)), "ocr_pages": ocr_pages, "image_count": len(images), "paragraph_count": sum(len(re.findall(r"<p(?:\s|>)", x)) for x in bodies), "full_text_missing": any(("no extractable" in w or "OCR failed" in w or "OCR timed out" in w) for w in warnings), "warnings": warnings}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    return result


def _validate(path: Path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "mimetype" not in names or archive.read("mimetype") != b"application/epub+zip": raise ValueError("Invalid EPUB mimetype")
        if any(n.startswith("/") or ".." in PurePosixPath(n).parts for n in names): raise ValueError("Unsafe EPUB path")
        opf = ET.fromstring(archive.read("EPUB/package.opf")); ns = {"o":"http://www.idpf.org/2007/opf"}
        ids = set()
        for item in opf.findall(".//o:manifest/o:item", ns):
            href = item.attrib["href"]
            if href.startswith("/") or ".." in PurePosixPath(href).parts or f"EPUB/{href}" not in names: raise ValueError(f"Broken manifest reference: {href}")
            ids.add(item.attrib["id"])
        if any(ref.attrib["idref"] not in ids for ref in opf.findall(".//o:spine/o:itemref", ns)): raise ValueError("Broken spine reference")
        for name in names:
            if name.endswith((".xhtml", ".xml", ".opf", ".ncx")): ET.fromstring(archive.read(name))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert a local PDF to EPUB")
    parser.add_argument("source", type=Path); parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path, required=True); parser.add_argument("--max-pages", type=int, default=1000); parser.add_argument("--title")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(convert(args.source, args.output, args.report, args.max_pages, args.title), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"conversion failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
