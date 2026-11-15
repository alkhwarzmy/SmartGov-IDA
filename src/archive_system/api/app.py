"""Local API and bundled Arabic UI. No CDN-backed documentation endpoints."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID
from datetime import date
from urllib.parse import urlsplit
from fastapi import FastAPI, File, UploadFile, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field
from archive_system.application.service import ArchiveService, ConflictError, NotFoundError
from archive_system.archiving.files import IntegrityError
from archive_system.config import Settings
from archive_system.ingestion.files import InputError

STATIC=Path(__file__).resolve().parents[1]/'ui/static'

class ReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    target: Literal['classification','field','page']
    value: str | None=Field(default=None,max_length=200000)
    field_id: UUID | None=None
    page_number: int | None=Field(default=None,ge=1)
    calendar: Literal['unknown','gregorian','hijri']='unknown'
    note: str | None=Field(default=None,max_length=1000)
    expected_run_id: UUID

class ConfirmInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_run_id: UUID
    acknowledged: Literal[True]


def create_app(settings: Settings | None=None,service: ArchiveService | None=None):
    configuration=settings or Settings.from_environment()
    @asynccontextmanager
    async def lifespan(app):
        app.state.service=service or ArchiveService(configuration)
        try:yield
        finally:
            if service is None:app.state.service.close()
    app=FastAPI(title='SmartGov Local Archive',version='0.2.0',docs_url=None,redoc_url=None,lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

    @app.middleware('http')
    async def local_boundary(request: Request,call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('x-archive-request')!='local':return JSONResponse({'detail':'LOCAL_REQUEST_HEADER_REQUIRED'},status_code=403)
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'CROSS_ORIGIN_REQUEST_REJECTED'},status_code=403)
            max_body=configuration.max_bytes+128*1024
            declared=request.headers.get('content-length')
            if declared:
                try:
                    if int(declared)>max_body:return JSONResponse({'detail':'FILE_TOO_LARGE'},status_code=413)
                except ValueError:return JSONResponse({'detail':'INVALID_CONTENT_LENGTH'},status_code=400)
            size=0;parts=[]
            async for chunk in request.stream():
                size+=len(chunk)
                if size>max_body:return JSONResponse({'detail':'FILE_TOO_LARGE'},status_code=413)
                parts.append(chunk)
            request._body=b''.join(parts)
        response=await call_next(request)
        response.headers['Content-Security-Policy']="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,error):return JSONResponse({'detail':'INVALID_REQUEST'},status_code=422)
    @app.exception_handler(NotFoundError)
    async def missing(request,error):return JSONResponse({'detail':str(error)},status_code=404)
    @app.exception_handler(ConflictError)
    async def conflict(request,error):return JSONResponse({'detail':str(error)},status_code=409)
    @app.exception_handler(IntegrityError)
    async def integrity(request,error):return JSONResponse({'detail':str(error)},status_code=409)
    @app.exception_handler(InputError)
    async def invalid_file(request,error):return JSONResponse({'detail':str(error)},status_code=413 if str(error)=='FILE_TOO_LARGE' else 415)

    @app.get('/api/health')
    def health():
        with app.state.service.store.connect() as connection:
            connection.execute('SELECT count(*) FROM document_fts').fetchone()
        return dict(status='ok',max_bytes=configuration.max_bytes,max_pages=configuration.max_pages,review_required=True)

    @app.get('/api/documents')
    def search(q: str=Query(default='',max_length=300),category: Literal['letter','invoice','unknown'] | None=None,status: Literal['received','processing','needs_review','completed','failed'] | None=None,
               date_from: date | None=None,date_to: date | None=None,limit: int=Query(default=50,ge=1,le=100),offset: int=Query(default=0,ge=0,le=100000)):
        if date_from and date_to and date_from>date_to:return JSONResponse({'detail':'INVALID_DATE_RANGE'},status_code=422)
        rows=app.state.service.search(q,category,status,date_from.isoformat() if date_from else None,date_to.isoformat() if date_to else None,limit,offset)
        return dict(items=[{k:d[k] for k in ('id','original_name','media_type','size_bytes','page_count','status','created_at','updated_at','classification')} for d in rows],limit=limit,offset=offset)

    @app.post('/api/documents',status_code=201)
    def upload(file: UploadFile=File(...)):
        try:return app.state.service.ingest(file.file,file.filename or 'document')
        finally:file.file.close()

    @app.get('/api/documents/{document_id}')
    def detail(document_id: UUID):return app.state.service.get(document_id)

    @app.get('/api/documents/{document_id}/runs/{run_id}')
    def run(document_id: UUID,run_id: UUID):return app.state.service.run_detail(document_id,run_id)

    @app.get('/api/documents/{document_id}/original')
    def original(document_id: UUID,download: bool=False):
        path,document=app.state.service.original(document_id)
        return FileResponse(path,media_type=document.media_type,filename=document.original_name,content_disposition_type='attachment' if download else 'inline')

    @app.get('/api/documents/{document_id}/preview')
    def preview(document_id: UUID,page: int=Query(default=1,ge=1)):
        return Response(app.state.service.preview(document_id,page),media_type='image/png')

    @app.post('/api/documents/{document_id}/retry')
    def retry(document_id: UUID):return app.state.service.retry(document_id)

    @app.post('/api/documents/{document_id}/reviews')
    def review(document_id: UUID,body: ReviewInput):
        if (body.target=='field')!=(body.field_id is not None) or (body.target=='page')!=(body.page_number is not None):
            return JSONResponse({'detail':'INVALID_REVIEW_TARGET'},status_code=422)
        return app.state.service.review(document_id,body.target,body.value,field_id=body.field_id,page_number=body.page_number,calendar=body.calendar,note=body.note,expected_run_id=body.expected_run_id)

    @app.post('/api/documents/{document_id}/confirm')
    def confirm(document_id: UUID,body: ConfirmInput):return app.state.service.confirm(document_id,body.expected_run_id)

    @app.get('/')
    def index():return FileResponse(STATIC/'index.html',media_type='text/html')
    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app
