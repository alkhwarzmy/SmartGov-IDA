from uuid import uuid4
import pytest
from archive_system.domain.models import Page
from archive_system.classification.rules import classify
from archive_system.extraction.rules import extract
from archive_system.validation.fields import validate_value

def pages(text):return [Page(document_id=uuid4(),run_id=uuid4(),number=1,text=text,source='digital')]

def test_classification_and_conflicting_markers():
    for text,expected in [('فاتورة رقم ١','invoice'),('رقم الخطاب: 12','letter'),('رواية عن نبات','unknown'),('فاتورة وخطاب إداري','unknown')]:
        assert classify(pages(text),uuid4(),uuid4()).category==expected

def test_extraction_anchors_and_ambiguity():
    fields=extract(pages('فاتورة\nرقم الفاتورة: INV-001\nالتاريخ: 2026-11-15\nرقم هاتف: 9999\nالإجمالي: 125.50\nالإجمالي: 152.50'),'invoice',uuid4(),uuid4())
    result={f.name:f for f in fields}
    assert result['invoice_number'].normalized_value=='INV-001'
    assert result['date'].status=='needs_review' and result['date'].calendar=='unknown'
    assert result['supplier'].status=='missing'
    assert result['total'].status=='ambiguous' and result['total'].normalized_value is None
    assert len(result['total'].sources)==2

@pytest.mark.parametrize('name,value,calendar,status,normalized',[
 ('total','١٬٢٥٠٫٥٠','unknown','valid','1250.50'),('total','12,34','unknown','invalid',None),
 ('total','NaN','unknown','invalid',None),('total','-1','unknown','invalid',None),
 ('currency','ريال','unknown','needs_review','ريال'),('currency','SAR','unknown','valid','SAR'),
 ('date','2026-02-30','gregorian','invalid','2026-02-30'),('date','2026-11-15','unknown','needs_review','2026-11-15'),
 ('date','1448-11-15','hijri','needs_review','1448-11-15'),('date','2026-11-15 ميلادي','unknown','valid','2026-11-15'),
 ('date','12/11/2026','unknown','needs_review','12/11/2026'),('total','125.50 / 152.50','unknown','ambiguous',None),
 ('supplier','غير مقروء','unknown','needs_review',None),('invoice_number','ABC','unknown','invalid','ABC')])
def test_value_validation(name,value,calendar,status,normalized):
    check=validate_value(name,value,calendar)
    assert check.status==status and check.normalized==normalized

def test_lost_layout_separator_yields_review_candidate_not_automatic_validity():
    result=extract(pages('Invoice\nSupplier  Widget Company\nInvoice number  B-499\nTotal  99.50\nMention of supplier elsewhere  OTHER'),'invoice',uuid4(),uuid4())
    fields={f.name:f for f in result}
    assert fields['supplier'].normalized_value=='Widget Company'
    assert fields['supplier'].status=='needs_review'
    assert fields['supplier'].reason=='layout_gap_without_separator'
    assert fields['total'].normalized_value=='99.50'
    assert fields['invoice_number'].raw_value=='B-499'
