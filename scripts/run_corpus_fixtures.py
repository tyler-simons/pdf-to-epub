#!/usr/bin/env python3
"""Generate local scan/encrypted/page-limit probes; no third-party fixtures bundled."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET
import pymupdf as fitz
from run_corpus import run_converter
ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'.corpus-cache'

def main():
    CACHE.mkdir(exist_ok=True)
    source=fitz.open(); page=source.new_page()
    page.insert_text((72,90),'A scanned page should become searchable text.',fontsize=18)
    page.insert_text((72,130),'The lighthouse keeper reads books by the sea.',fontsize=18)
    pix=page.get_pixmap(matrix=fitz.Matrix(2,2))
    scan=fitz.open(); scan.new_page().insert_image(page.rect,stream=pix.tobytes('png'))
    scan.save(CACHE/'generated-scan.pdf'); scan.close()
    source.save(CACHE/'generated-encrypted.pdf',encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='password')
    source.close()
    long=fitz.open()
    for _ in range(1501): long.new_page()
    long.save(CACHE/'generated-over-limit.pdf'); long.close()
    (CACHE/'generated-malformed.pdf').write_bytes(b'%PDF-1.7\nnot a document\n')
    results=[]
    for name,expected in [('scan',None),('encrypted','password_required'),('over-limit','page_limit'),('malformed','conversion_failed')]:
        path=CACHE/('generated-'+name+'.pdf'); report=path.with_suffix('.report.json'); epub=path.with_suffix('.epub')
        epub.unlink(missing_ok=True)
        proc=run_converter([sys.executable,'-m','app.runner',str(path),str(epub),'--report',str(report),'--max-pages','1500'],60)
        data=json.loads(report.read_text())
        passed=(proc.returncode!=0 and data.get('error_code')==expected and not epub.exists()) if expected else (proc.returncode==0 and data.get('ocr_pages')==1 and not data.get('full_text_missing'))
        if name == 'scan' and epub.exists():
            with zipfile.ZipFile(epub) as archive:
                node = ET.fromstring(archive.read('EPUB/text/p1.xhtml'))
                text = ' '.join(''.join(node.itertext()).split())
            passed = passed and 'lighthouse keeper reads books by the sea' in text.casefold()
        results.append({'id':'generated-'+name,'passed':passed,'exit_code':proc.returncode,'report':data})
    (CACHE/'fixture-results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))
    return int(not all(r['passed'] for r in results))
if __name__=='__main__': raise SystemExit(main())
