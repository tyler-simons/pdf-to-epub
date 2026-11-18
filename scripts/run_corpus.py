#!/usr/bin/env python3
"""Opt-in public PDF corpus. No AI/API calls; downloads and books stay in ignored cache.

Run: python scripts/run_corpus.py [--offline] [--only ID ...]
Source SHA256 values are pinned in manifest.json after the first verified run.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
import signal
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import zipfile
from xml.etree import ElementTree as ET
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 40 * 1024 * 1024


def run_converter(command, timeout):
    """No credentials inherited; kill converter and OCR subprocesses on timeout."""
    allowed = ('PATH', 'PYTHONPATH', 'LANG', 'LC_ALL', 'OMP_THREAD_LIMIT', 'LEAF_CHILD_SECONDS')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.setdefault('OMP_THREAD_LIMIT', '1')
    env['LEAF_CHILD_SECONDS'] = str(timeout)
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def download(url, destination):
    request = urllib.request.Request(url, headers={'User-Agent': 'Rebind-PDF-corpus/1.0 (local compatibility testing)'})
    partial = destination.with_suffix('.part')
    try:
        with urllib.request.urlopen(request, timeout=40) as response, partial.open('wb') as output:
            size = 0
            while chunk := response.read(65536):
                size += len(chunk)
                if size > LIMIT:
                    raise ValueError('Input exceeds 40 MiB corpus limit')
                output.write(chunk)
        if b'%PDF-' not in partial.read_bytes()[:1024]:
            raise ValueError('Response is not a PDF')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def audit(pdf, epub, report):
    with fitz.open(pdf) as doc:
        pages = len(doc)
        source_text = ' '.join(p.get_text() for p in doc)
        widgets = sum(sum(1 for _ in p.widgets()) for p in doc)
        blank = sum(not p.get_text().strip() for p in doc)
    texts, image_pages, text_pages, fallback_pages = [], 0, 0, 0
    with zipfile.ZipFile(epub) as archive:
        names = [n for n in archive.namelist() if re.search(r'/text/p\d+\.xhtml$', n)]
        for name in names:
            node = ET.fromstring(archive.read(name))
            paragraphs = [''.join(p.itertext()) for p in node.iter() if p.tag.rsplit('}',1)[-1] in ('p','h2')]
            text = ' '.join(paragraphs)
            texts.append(text)
            image_pages += any(p.tag.rsplit('}',1)[-1]=='img' for p in node.iter())
            text_pages += bool(text.strip())
            fallback_pages += any(p.tag.rsplit('}',1)[-1]=='img' and ('preserved as an image' in p.get('alt','') or p.get('alt','').startswith('Scanned page')) for p in node.iter())
    output_text = ' '.join(texts)
    tokens = lambda s: Counter(re.findall(r'\w+', s.casefold()))
    source_tokens, output_tokens = tokens(source_text), tokens(output_text)
    denominator = sum(source_tokens.values())
    retention = sum((source_tokens & output_tokens).values()) / denominator if denominator else None
    normalized = lambda s: ' '.join(re.findall(r'\w+', s.casefold()))
    original, converted = normalized(source_text), normalized(output_text)
    words = original.split()
    anchors = {'first_anchor_present': ' '.join(words[:6]) in converted, 'last_anchor_present': ' '.join(words[-6:]) in converted}
    if pdf.stem in ('w3c-columns-word', 'w3c-columns-oo'):
        sequence = ['Nullam a est eget ipsum', '2. Nulla malesuada']
        positions = [converted.find(normalized(anchor)) for anchor in sequence]
        anchors['column_sequence_expected'] = sequence
        anchors['column_sequence_pass'] = all(p >= 0 for p in positions) and positions == sorted(positions)
    return {**anchors,'source_pages':pages,'xhtml_pages':len(names),'coverage':len(names)/pages,'source_text_chars':len(source_text),'output_text_chars':len(output_text),'source_blank_text_pages':blank,'form_widgets':widgets,'image_pages':image_pages,'image_page_fraction':image_pages/pages,'fallback_pages':fallback_pages,'fallback_fraction':fallback_pages/pages,'output_text_pages':text_pages,'token_retention':round(retention,3) if retention is not None else None,'full_text_missing':report.get('full_text_missing'),'ocr_pages':report.get('ocr_pages'),'warning_count':len(report.get('warnings',[])),'warnings':report.get('warnings',[]),'fidelity':'requires visual review; token retention does not establish reading order or correctness'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--only',nargs='+')
    parser.add_argument('--timeout',type=int,default=240)
    parser.add_argument('--pin',action='store_true',help='Record SHA256 of successfully downloaded sources')
    args=parser.parse_args()
    manifest_path=ROOT/'tests/corpus/manifest.json'
    manifest=json.loads(manifest_path.read_text())
    cache=ROOT/'.corpus-cache'; cache.mkdir(exist_ok=True)
    results=[]
    for source in manifest['sources']:
        if args.only and source['id'] not in args.only: continue
        item={'id':source['id'],'url':source['url']}; start=time.monotonic()
        pdf=cache/(source['id']+'.pdf'); epub=pdf.with_suffix('.epub'); report_path=pdf.with_suffix('.report.json')
        try:
            if not pdf.exists():
                if args.offline: raise ValueError('Source absent from offline cache')
                download(source['url'],pdf)
            if pdf.stat().st_size>LIMIT: raise ValueError('Input exceeds 40 MiB corpus limit')
            digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
            item.update(sha256=digest,input_bytes=pdf.stat().st_size)
            if source.get('sha256') and source['sha256']!=digest: raise ValueError('SHA256 mismatch; review upstream change before repinning')
            if args.pin: source['sha256']=digest
            epub.unlink(missing_ok=True); report_path.unlink(missing_ok=True)
            proc=run_converter([sys.executable,'-m','app.runner',str(pdf),str(epub),'--report',str(report_path),'--max-pages','1500'],args.timeout)
            report=json.loads(report_path.read_text()) if report_path.exists() else {}
            item.update(exit_code=proc.returncode,status='structural-pass' if proc.returncode==0 else 'conversion-failed')
            if proc.returncode: item['error']=proc.stderr[-2000:]; item['report']=report
            else:
                item.update(audit(pdf,epub,report))
                if item['coverage']!=1: item['status']='coverage-failed'
        except subprocess.TimeoutExpired: item.update(status='timeout',error=f'Conversion exceeded {args.timeout}s')
        except Exception as exc: item.update(status='error',error=str(exc))
        item['seconds']=round(time.monotonic()-start,2); results.append(item)
        print(json.dumps({k:v for k,v in item.items() if k not in ('warnings','fidelity','url')}),flush=True)
        (cache/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    if args.pin: manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    return int(any(x['status']!='structural-pass' for x in results))

if __name__=='__main__': raise SystemExit(main())
