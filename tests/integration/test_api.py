from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from archive_system.api.app import create_app
from test_storage import service

HEADERS={'X-Archive-Request':'local'}
@pytest.fixture
def client(service):
    with TestClient(create_app(settings=service.settings,service=service)) as c:yield c

def test_api_full_cycle(client):
    raw=Path('/workspace/smartgov-corpus/v1/development-invoice-01-digital.pdf').read_bytes()
    result=client.post('/api/documents',files={'file':('invoice.pdf',raw,'application/pdf')},headers=HEADERS)
    assert result.status_code==201
    doc=result.json()['document'];identity=doc['id'];run=doc['current_run_id']
    field=next(f for f in doc['fields'] if f['name']=='date')
    response=client.post(f'/api/documents/{identity}/reviews',json=dict(target='field',value='2026-11-15',calendar='gregorian',field_id=field['id'],expected_run_id=run),headers=HEADERS)
    assert response.status_code==200
    confirmed=client.post(f'/api/documents/{identity}/confirm',json=dict(expected_run_id=run,acknowledged=True),headers=HEADERS)
    assert confirmed.status_code==200 and confirmed.json()['status']=='completed'
    assert client.get('/api/documents',params={'q':'INV-101','category':'invoice','status':'completed'}).json()['items'][0]['id']==identity
    assert client.get(f'/api/documents/{identity}/original').content==raw
    assert client.get(f'/api/documents/{identity}/original?download=true').headers['content-disposition'].startswith('attachment')
    assert client.post('/api/documents',files={'file':('duplicate.pdf',raw)},headers=HEADERS).json()['duplicate'] is True

def test_local_ui_and_request_boundaries(client):
    page=client.get('/')
    assert page.status_code==200 and 'dir="rtl"' in page.text
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert client.get('/static/app.js').status_code==200
    assert client.get('/docs').status_code==404
    assert client.get('/api/health').json()['status']=='ok'
    assert client.post('/api/documents',files={'file':('bad.pdf',b'%PDF-bad')}).status_code==403
    assert client.post('/api/documents',files={'file':('bad.pdf',b'%PDF-bad')},headers={**HEADERS,'Origin':'https://outside.invalid'}).status_code==403
    assert client.get('/api/health',headers={'host':'outside.invalid'}).status_code==400
    assert client.post('/api/documents',content=b'x',headers={**HEADERS,'content-length':str(21*1024**2)}).status_code==413
    assert client.get('/api/documents?date_from=invalid').status_code==422

def test_preview_is_rendered_locally_and_history_is_accessible(client):
    raw=Path('/workspace/smartgov-corpus/v1/development-letter-01-digital.pdf').read_bytes()
    doc=client.post('/api/documents',files={'file':('letter.pdf',raw)},headers=HEADERS).json()['document']
    preview=client.get(f'/api/documents/{doc["id"]}/preview')
    assert preview.status_code==200 and preview.content.startswith(b'\x89PNG\r\n\x1a\n')
    assert client.get(f'/api/documents/{doc["id"]}/preview?page=99').status_code==409
    history=client.get(f'/api/documents/{doc["id"]}/runs/{doc["current_run_id"]}')
    assert history.status_code==200 and history.json()['pages'][0]['source']=='digital'
