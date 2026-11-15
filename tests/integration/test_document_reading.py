import hashlib
import os
from pathlib import Path
from uuid import uuid4
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from archive_system.config import Settings
from archive_system.document_reading.reader import DocumentReader, ReadingError
from archive_system.ocr.tesseract import get_tesseract

CORPUS=Path(os.environ.get('SMARTGOV_CORPUS','/workspace/smartgov-corpus/v1'))
MODELS=Path(os.environ.get('SMARTGOV_TESSDATA','/workspace/smartgov-models/tesseract'))

@pytest.fixture(scope='module')
def engine():
    return get_tesseract(MODELS)

@pytest.fixture
def reader(engine):return DocumentReader(engine)

def read(reader,path):return reader.read(path,document_id=uuid4(),run_id=uuid4())

@pytest.mark.parametrize('suffix,source',[('.png','ocr'),('.jpg','ocr'),('-scanned.pdf','ocr'),('-digital.pdf','digital')])
def test_actual_arabic_reading_and_original_unchanged(reader,suffix,source):
    path=CORPUS/f'development-letter-01{suffix}'
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=read(reader,path)
    page=result.pages[0]
    assert page.source==source
    assert 'خطاب' in page.text
    assert '2026-11-15' in page.text
    if source=='ocr':
        assert page.regions
        assert all(r.box.unit=='pixel' and r.box.width>0 for r in page.regions)
    else:
        assert 'L-101' in page.text
        assert 'طلب قرطاسية تجريبية' in page.text
        assert result.timings_seconds['ocr']==0
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before

def test_engine_reused(engine,reader):
    assert engine is get_tesseract(MODELS)
    api=engine._api
    read(reader,CORPUS/'development-letter-01.png')
    read(reader,CORPUS/'development-invoice-01.png')
    assert engine._api is api

def test_blank_image_is_empty_result_not_crash(reader,tmp_path):
    path=tmp_path/'blank.png';Image.new('RGB',(300,300),'white').save(path)
    result=read(reader,path)
    assert result.pages[0].text.strip()==''
    assert 'ocr_empty' in result.quality_reasons[1]

@pytest.mark.parametrize('payload,code',[(b'','EMPTY_FILE'),(b'not a document','UNSUPPORTED_OR_CORRUPT_FILE'),(b'%PDF-broken','PDF_READING_FAILED'),(b'\x89PNG\r\n\x1a\nbroken','CORRUPT_OR_OVERSIZED_IMAGE')])
def test_invalid_input(reader,tmp_path,payload,code):
    path=tmp_path/'bad';path.write_bytes(payload)
    with pytest.raises(ReadingError,match=code):read(reader,path)

def test_encrypted_empty_and_page_limit(reader,tmp_path):
    for name,pages,encrypted,code in [('empty',0,False,'EMPTY_PDF'),('encrypted',1,True,'ENCRYPTED_PDF'),('large',11,False,'PAGE_LIMIT_EXCEEDED')]:
        writer=PdfWriter()
        for _ in range(pages):writer.add_blank_page(width=100,height=100)
        if encrypted:writer.encrypt('test-fixture-password')
        path=tmp_path/f'{name}.pdf';writer.write(path)
        with pytest.raises(ReadingError,match=code):read(reader,path)

def test_file_and_pixel_limits(engine,tmp_path):
    path=tmp_path/'large.png';Image.new('RGB',(50,50),'white').save(path)
    with pytest.raises(ReadingError,match='FILE_TOO_LARGE'):
        read(DocumentReader(engine,Settings(max_bytes=10)),path)
    with pytest.raises(ReadingError,match='IMAGE_PIXEL_LIMIT'):
        read(DocumentReader(engine,max_pixels=100),path)
    with pytest.raises(ReadingError,match='PDF_RENDER_PIXEL_LIMIT'):
        read(DocumentReader(engine,max_pixels=100),CORPUS/'development-letter-01-scanned.pdf')

def test_mixed_pdf_routes_each_page_independently(reader,tmp_path):
    writer=PdfWriter()
    writer.add_page(PdfReader(CORPUS/'development-letter-01-digital.pdf').pages[0])
    writer.add_page(PdfReader(CORPUS/'development-letter-01-scanned.pdf').pages[0])
    path=tmp_path/'mixed.pdf';writer.write(path)
    result=read(reader,path)
    assert [p.source for p in result.pages]==['digital','ocr']
    assert [p.number for p in result.pages]==[1,2]

def test_actual_pdf_with_dropped_latin_runs_uses_ocr(reader,tmp_path):
    from fpdf import FPDF
    pdf=FPDF();pdf.add_page()
    pdf.add_font('Arabic','', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    pdf.set_font('Arabic',size=18);pdf.set_text_shaping(True)
    for line in ('خطاب إداري','رقم الخطاب: L-101','التاريخ: 2026-11-15'):
        pdf.cell(190,12,line,align='R',new_x='LMARGIN',new_y='NEXT')
    path=tmp_path/'mixed-runs.pdf';pdf.output(str(path))
    result=read(reader,path)
    assert result.pages[0].source=='ocr'
    assert 'lost_mixed_direction_runs' in result.quality_reasons[1]
