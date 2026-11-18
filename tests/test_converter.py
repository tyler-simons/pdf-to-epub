import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf as fitz
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.converter import convert


def test_digital_pdf_emphasis_bookmarks_and_valid_epub(tmp_path):
    source = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A chapter title", fontsize=20, fontname="hebo")
    page.insert_text((72, 110), "Ordinary prose for a small generated test.", fontsize=12)
    page.insert_text((72, 135), "More useful words follow here.", fontsize=12)
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Second page paragraph.", fontsize=12)
    doc.set_toc([[1, "Opening section", 1], [1, "Second section", 2]])
    doc.save(source)
    doc.close()

    target, report = tmp_path / "book.epub", tmp_path / "report.json"
    result = convert(source, target, report, title="Fixture")
    assert result["converted_pages"] == 2
    assert result["text_pages"] == 2
    assert result["full_text_missing"] is False
    with zipfile.ZipFile(target) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"
        page1 = archive.read("EPUB/text/p1.xhtml").decode()
        nav = archive.read("EPUB/nav.xhtml").decode()
        assert "Ordinary prose" in page1
        assert "Opening section" in nav
        ET.fromstring(page1)
        ET.fromstring(archive.read("EPUB/package.opf"))
    assert json.loads(report.read_text())["title"] == "Fixture"


def test_complex_layout_is_preserved_and_page_limit_rejected(tmp_path):
    source = tmp_path / "table.pdf"
    doc = fitz.open()
    page = doc.new_page()
    for y, row in enumerate(("Name       Value", "Alpha      12", "Beta       15", "Gamma      18")):
        page.insert_text((72, 72 + y * 24), row)
    doc.new_page().insert_text((72, 72), "Beyond page cap")
    doc.save(source); doc.close()
    with pytest.raises(ValueError, match="exceeding max_pages"):
        convert(source, tmp_path / "out.epub", tmp_path / "report.json", max_pages=1)
    doc = fitz.open(source)
    doc.delete_page(1)
    doc.save(tmp_path / "table-only.pdf")
    doc.close()
    result = convert(tmp_path / "table-only.pdf", tmp_path / "only.epub", tmp_path / "only.json")
    assert result["converted_pages"] == 1
    assert result["image_count"] == 1
    assert any("table" in w for w in result["warnings"])


def test_cross_page_join_closes_paragraph_and_preserves_xml(tmp_path):
    source = tmp_path / "join.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "This sentence begins on page one and continues")
    doc.new_page().insert_text((72, 72), "with a lowercase phrase on page two.")
    doc.save(source); doc.close()
    target = tmp_path / "join.epub"
    convert(source, target, tmp_path / "join.json")
    with zipfile.ZipFile(target) as archive:
        one = ET.fromstring(archive.read("EPUB/text/p1.xhtml"))
        two = ET.fromstring(archive.read("EPUB/text/p2.xhtml"))
        ns = {"x": "http://www.w3.org/1999/xhtml"}
        assert "continues with a lowercase" in "".join(one.itertext())
        assert "lowercase phrase" not in "".join(two.itertext())


def test_invalid_xml_controls_are_removed_and_images_follow_geometry(tmp_path):
    from PIL import Image
    source = tmp_path / "image.pdf"
    image_path = tmp_path / "pixel.png"
    Image.new("RGB", (20, 20), "red").save(image_path)
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((72, 100), "After image \x01 safe")
    page.insert_image(fitz.Rect(72, 40, 120, 88), filename=str(image_path))
    doc.save(source); doc.close()
    target = tmp_path / "image.epub"
    convert(source, target, tmp_path / "image.json", title="Bad \x01 title")
    with zipfile.ZipFile(target) as archive:
        page_xml = archive.read("EPUB/text/p1.xhtml")
        root = ET.fromstring(page_xml)
        text = "".join(root.itertext())
        assert "After image safe" in " ".join(text.split())
        package = archive.read("EPUB/package.opf")
        assert b"Bad   title" not in package
        assert page_xml.index(b"<figure>") < page_xml.index(b"After image")


def test_scan_without_tesseract_keeps_page_image(tmp_path, monkeypatch):
    source = tmp_path / "scan.pdf"
    doc = fitz.open(); doc.new_page().draw_rect(fitz.Rect(30, 30, 300, 300), color=(0, 0, 0)); doc.save(source); doc.close()
    monkeypatch.setattr("app.converter.shutil.which", lambda _: None)
    result = convert(source, tmp_path / "scan.epub", tmp_path / "scan.json")
    assert result["image_count"] == 1
    assert result["full_text_missing"] is True
    assert result["ocr_pages"] == 0


def test_max_pages_must_be_positive(tmp_path):
    source = tmp_path / "empty.pdf"
    doc = fitz.open(); doc.new_page(); doc.save(source); doc.close()
    with pytest.raises(ValueError, match="max_pages"):
        convert(source, tmp_path / "out.epub", tmp_path / "report.json", max_pages=0)


def test_page_rule_is_not_a_table_and_line_geometry_does_not_crash(tmp_path):
    source=tmp_path/'rule.pdf';doc=fitz.open();page=doc.new_page()
    page.draw_line((30,30),(500,30))
    for i in range(5):page.insert_text((72,80+i*40),'A normal paragraph under a single page rule.')
    doc.save(source);doc.close()
    result=convert(source,tmp_path/'rule.epub',tmp_path/'report.json')
    assert result['image_count']==0


def test_scanned_text_uses_local_ocr_when_available(tmp_path):
    import shutil
    if not shutil.which('tesseract'):pytest.skip('Tesseract is optional locally')
    original=fitz.open();p=original.new_page(width=500,height=240)
    p.insert_text((40,90),'Reading makes the world larger.',fontsize=22)
    pixels=p.get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
    scan=fitz.open();p=scan.new_page(width=500,height=240)
    p.insert_image(p.rect,stream=pixels);source=tmp_path/'scan-text.pdf';scan.save(source)
    original.close();scan.close()
    target=tmp_path/'scan.epub';result=convert(source,target,tmp_path/'scan.json')
    assert result['ocr_pages']==1
    with zipfile.ZipFile(target) as z:assert b'Reading makes' in z.read('EPUB/text/p1.xhtml')


def test_two_columns_read_down_each_column_with_spanning_heading(tmp_path):
    source = tmp_path / 'columns.pdf'
    doc = fitz.open(); page = doc.new_page(width=600, height=800)
    page.insert_text((40,40), 'A heading across the entire page width for both columns', fontsize=18)
    page.insert_text((40,100), 'Left column first paragraph.')
    page.insert_text((40,300), 'Left column last paragraph.')
    page.insert_text((330,100), 'Right column first paragraph.')
    page.insert_text((330,200), 'Right column last paragraph.')
    doc.save(source);doc.close()
    output = tmp_path / 'columns.epub'
    convert(source, output, tmp_path / 'report.json')
    with zipfile.ZipFile(output) as z:
        text = ''.join(ET.fromstring(z.read('EPUB/text/p1.xhtml')).itertext())
    assert text.index('heading across') < text.index('Left column first')
    assert text.index('Left column last') < text.index('Right column first')
    assert text.index('Right column first') < text.index('Right column last')


def test_columns_allow_small_font_bbox_overlap():
    from app.converter import _reading_order
    items = [((57,69,307,95),'left first',True),
             ((57,372,280,386),'left last',True),
             ((306,69,520,90),'right first',True),
             ((327,228,520,255),'right last',True)]
    assert [item[1] for item in _reading_order(items,595)] == ['left first','left last','right first','right last']
