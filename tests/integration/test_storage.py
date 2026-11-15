from io import BytesIO
from pathlib import Path
from uuid import uuid4
import hashlib
import pytest
from archive_system.application.service import ArchiveService, ConflictError
from archive_system.config import Settings
from archive_system.ingestion.files import InputError
from archive_system.archiving.files import IntegrityError
from archive_system.domain.models import Document, ProcessingRun, Status

CORPUS=Path('/workspace/smartgov-corpus/v1')
@pytest.fixture
def service(tmp_path):
    app=ArchiveService(Settings(runtime_dir=tmp_path/'data',model_dir=Path('/workspace/smartgov-models/tesseract')))
    yield app
    app.close()

def upload(service,name='development-letter-01-digital.pdf'):
    with (CORPUS/name).open('rb') as stream:return service.ingest(stream,name)

def test_storage_duplicate_retry_history_and_original(service):
    result=upload(service);document=result['document']
    assert document['status']=='needs_review'
    assert document['pages'][0]['source']=='digital'
    original,_=service.original(document['id'])
    assert hashlib.sha256(original.read_bytes()).hexdigest()==document['sha256']
    duplicate=upload(service)
    assert duplicate['duplicate'] and duplicate['document']['id']==document['id']
    assert len(duplicate['document']['runs'])==1
    retry=service.retry(document['id'])
    assert len(retry['runs'])==2
    with service.store.connect() as c:
        assert c.execute('SELECT count(*) FROM pages').fetchone()[0]==2
    assert service.search('قرطاسية')[0]['id']==document['id']

def test_failure_keeps_original_and_retries_do_not_hide_failure(service):
    document=service.ingest(BytesIO(b'%PDF-broken'),'bad.pdf')['document']
    assert document['status']=='failed'
    assert document['runs'][0]['errors']==['PDF_READING_FAILED']
    assert service.original(document['id'])[0].read_bytes()==b'%PDF-broken'
    assert len(service.retry(document['id'])['runs'])==2

def test_limits_filename_and_integrity(service):
    with pytest.raises(InputError):service.ingest(BytesIO(b'not supported'),'data.exe')
    assert not list((service.root/'incoming').iterdir())
    result=upload(service);document=result['document']
    original,_=service.original(document['id']);original.write_bytes(b'changed')
    with pytest.raises(IntegrityError):service.original(document['id'])
    assert service.retry(document['id'])['runs'][-1]['errors']==['ORIGINAL_INTEGRITY_ERROR']

def test_one_process_and_interrupted_recovery(service):
    with pytest.raises(ConflictError):ArchiveService(service.settings)
    result=upload(service);identity=result['document']['id']
    with service.store.connect() as c:
        row=c.execute('SELECT payload FROM documents WHERE id=?',(identity,)).fetchone()
        document=Document.model_validate_json(row[0]).model_copy(update={'status':Status.PROCESSING})
        c.execute('UPDATE documents SET status=?,payload=? WHERE id=?',('processing',document.model_dump_json(),identity))
    service.close()
    second=ArchiveService(service.settings)
    try:assert second.get(identity)['status']=='failed'
    finally:second.close()

def test_original_placed_before_interrupted_db_commit_is_recovered(service):
    identity=uuid4();path=service.root/'originals'/f'{identity}.pdf';path.write_bytes(b'%PDF-orphan')
    service.close();second=ArchiveService(service.settings)
    try:
        recovered=second.get(identity)
        assert recovered['status']=='failed'
        assert recovered['runs'][0]['errors']==['INTERRUPTED_INGESTION']
        assert second.original(identity)[0].read_bytes()==b'%PDF-orphan'
    finally:second.close()
