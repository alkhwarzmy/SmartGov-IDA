from pathlib import Path
import pytest
from archive_system.application.service import ConflictError
from test_storage import service, upload

def correct_date(service,doc):
    field=next(f for f in doc['fields'] if f['name']=='date')
    return service.review(doc['id'],'field','2026-11-15',field_id=field['id'],calendar='gregorian',expected_run_id=doc['current_run_id'])

def test_review_search_confirm_and_engine_history(service):
    doc=upload(service)['document'];original=doc['engine_fields']
    assert doc['classification']['category']=='letter'
    with pytest.raises(ConflictError,match='UNRESOLVED_FIELDS'):service.confirm(doc['id'],doc['current_run_id'])
    doc=correct_date(service,doc)
    subject=next(f for f in doc['fields'] if f['name']=='subject')
    doc=service.review(doc['id'],'field','مراجعة أرشيفية فريدة',field_id=subject['id'])
    assert doc['engine_fields']==original
    assert service.search('أرشيفية')[0]['id']==doc['id']
    assert not service.search('evil OR 1=1')
    assert service.search(date_from='2026-11-14',date_to='2026-11-16')[0]['id']==doc['id']
    assert not service.search(date_from='2027-01-01')
    assert service.confirm(doc['id'],doc['current_run_id'])['status']=='completed'
    service.review(doc['id'],'field','تعديل لاحق',field_id=subject['id'])
    assert service.get(doc['id'])['status']=='needs_review'

def test_ocr_requires_page_review_and_field_recheck_after_text_edit(service):
    doc=upload(service,'development-letter-01.png')['document']
    with pytest.raises(ConflictError,match='OCR_PAGES'):service.confirm(doc['id'],doc['current_run_id'])
    text=Path('/workspace/SmartGov-IDA/evaluation/data/development-letter-01.txt').read_text()
    doc=service.review(doc['id'],'page',text,page_number=1)
    assert doc['pages'][0]['text']!=doc['pages'][0]['effective_text']
    # Missing engine values are corrected independently after transcription.
    expected={'letter_number':'L-101','date':'2026-11-15','sender':'مكتب الأفق الوهمي','recipient':'إدارة النور الوهمية','subject':'طلب قرطاسية تجريبية'}
    for field in doc['fields']:
        doc=service.review(doc['id'],'field',expected[field['name']],field_id=field['id'],calendar='gregorian' if field['name']=='date' else 'unknown')
    assert service.confirm(doc['id'],doc['current_run_id'])['status']=='completed'
    assert service.search('تجريبية')[0]['id']==doc['id']

def test_missing_acknowledgement_classification_and_stale_reviews(service):
    doc=upload(service,'development-letter-02-digital.pdf')['document']
    field=next(f for f in doc['fields'] if f['name']=='subject')
    with pytest.raises(ConflictError,match='REQUIRES_NOTE'):service.review(doc['id'],'field',None,field_id=field['id'])
    service.review(doc['id'],'field',None,field_id=field['id'],note='غائب من الأصل')
    doc=correct_date(service,doc)
    assert service.confirm(doc['id'],doc['current_run_id'])['status']=='completed'
    changed=service.review(doc['id'],'classification','invoice')
    assert changed['engine_classification']['category']=='letter'
    assert changed['classification']['category']=='invoice'
    assert {f['name'] for f in changed['fields']}=={'invoice_number','date','supplier','total','currency'}
    old_run=doc['current_run_id'];service.retry(doc['id'])
    with pytest.raises(ConflictError,match='STALE'):service.review(doc['id'],'page','نص',page_number=1,expected_run_id=old_run)

def test_retry_exposes_previous_results_and_reverting_page_requires_recheck(service):
    doc=upload(service)['document'];old_run=doc['current_run_id']
    service.retry(doc['id'])
    previous=service.run_detail(doc['id'],old_run)
    assert previous['pages'][0]['text']==doc['pages'][0]['text']
    assert previous['fields']==doc['engine_fields']
    doc=service.get(doc['id']);original=doc['pages'][0]['text']
    service.review(doc['id'],'page',original+'\nسطر إضافي',page_number=1)
    for field in doc['fields']:
        service.review(doc['id'],'field',field['raw_value'],field_id=field['id'],calendar='gregorian' if field['name']=='date' else 'unknown')
    service.review(doc['id'],'page',original,page_number=1)
    with pytest.raises(ConflictError,match='RECHECK_FIELDS'):service.confirm(doc['id'],doc['current_run_id'])
