import hashlib
import json
from pathlib import Path
from uuid import uuid4
import pytest
from pydantic import ValidationError
from archive_system.config import Settings
from archive_system.domain.models import ExtractedField, FIELD_NAMES, DocumentType, ProcessingRun, Review

ROOT = Path(__file__).resolve().parents[2]

def test_corpus_integrity_and_split():
    records = json.loads((ROOT / 'evaluation/ground_truth.json').read_text())
    assert len(records) == 16
    assert len({r['id'] for r in records}) == 16
    sources = {r['id'] for r in json.loads((ROOT / 'evaluation/sources.json').read_text())}
    families = {}
    hashes = {}
    for r in records:
        assert r['source_id'] in sources
        data = (ROOT / r['text_path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == r['sha256']
        assert data.decode() == r['reference_text']
        families.setdefault(r['split'], set()).add(r['template_family'])
        hashes.setdefault(r['split'], set()).add(r['sha256'])
        assert {f['name'] for f in r['fields']} == set(FIELD_NAMES[DocumentType(r['category'])])
        for field in r['fields']:
            value = ExtractedField(id=uuid4(), document_id=uuid4(), run_id=uuid4(), **field)
            for source in value.sources:
                assert source.page == 1
                assert source.snippet in r['reference_text']
    assert not families['development'] & families['test']
    assert not hashes['development'] & hashes['test']
    assert sum(r['pages'] for r in records) <= 200
    assert sum((ROOT / r['text_path']).stat().st_size for r in records) <= 500 * 1024**2
    manifest = (ROOT / 'evaluation/test_manifest.sha256').read_text()
    assert manifest == ''.join(f"{r['sha256']}  {r['text_path']}\n" for r in records if r['split'] == 'test')

def field(**overrides):
    return dict(id=uuid4(), document_id=uuid4(), run_id=uuid4(), name='date', status='missing', **overrides)

@pytest.mark.parametrize('change', [dict(raw_value='guess'), dict(normalized_value='guess'), dict(sources=[dict(page=1,snippet='x')])])
def test_missing_rejects_guesses(change):
    with pytest.raises(ValidationError): ExtractedField(**field(**change))

@pytest.mark.parametrize('status', ['valid','ambiguous','invalid','needs_review'])
def test_nonmissing_requires_evidence(status):
    payload=field(); payload['status']=status
    with pytest.raises(ValidationError): ExtractedField(**payload)

def test_ambiguous_cannot_pick_value():
    payload=field(normalized_value='one',sources=[dict(page=1,snippet='one or two')]); payload['status']='ambiguous'
    with pytest.raises(ValidationError): ExtractedField(**payload)

def test_settings_limits_and_single_worker():
    assert Settings().max_bytes == 20 * 1024**2
    assert Settings().max_pages == 10
    for change in (dict(workers=2),dict(max_pages=0),dict(max_bytes=-1)):
        with pytest.raises(ValidationError): Settings(**change)

def test_failed_run_not_success():
    values=dict(id=uuid4(),document_id=uuid4(),engine='candidate',engine_version='unselected',settings={},started_at='2026-11-15T12:00:00Z',finished_at='2026-11-15T12:01:00Z',errors=['OCR_FAILURE'],status='completed')
    with pytest.raises(ValidationError): ProcessingRun(**values)
    values['status']='failed'
    assert ProcessingRun(**values).errors == ('OCR_FAILURE',)

def test_review_preserves_original_contract():
    original=ExtractedField(**field())
    review=Review(id=uuid4(),document_id=original.document_id,run_id=original.run_id,target='field',field_id=original.id,corrected_value='2026-11-15',created_at='2026-11-15T12:00:00Z')
    assert original.raw_value is None
    assert review.corrected_value == '2026-11-15'
    with pytest.raises(ValidationError): original.raw_value='replacement'

def test_frozen_test_annotations():
    records=json.loads((ROOT / 'evaluation/ground_truth.json').read_text())
    test=[r for r in records if r['split']=='test']
    digest=hashlib.sha256(json.dumps(test,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    assert digest == (ROOT / 'evaluation/test_annotations.sha256').read_text().strip()
