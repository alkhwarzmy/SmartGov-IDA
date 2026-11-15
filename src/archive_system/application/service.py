"""One local process, one serial unit of work, append-only extraction history."""
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
from threading import RLock
from time import perf_counter
from uuid import UUID, uuid4
from archive_system.archiving.files import verified_original, IntegrityError
from archive_system.config import Settings
from archive_system.database.store import Store
from archive_system.document_reading.reader import DocumentReader, ReadingError
from archive_system.domain.models import Document, Page, ProcessingRun, Status, Review, DocumentType, ExtractedField
from archive_system.classification.rules import classify
from archive_system.extraction.rules import extract
from archive_system.validation.fields import validate_value
from archive_system.ingestion.files import receive
from archive_system.ocr.tesseract import get_tesseract
from archive_system.search.text import normalize, literal_query

class NotFoundError(LookupError):pass
class ConflictError(ValueError):pass

def now():return datetime.now(timezone.utc)
def dump(value):return value.model_dump_json()

class ArchiveService:
    def __init__(self, settings: Settings, reader: DocumentReader | None = None):
        self.settings=settings;self.root=settings.runtime_dir.resolve()
        self.root.mkdir(parents=True,mode=0o700,exist_ok=True)
        (self.root/'originals').mkdir(mode=0o700,exist_ok=True)
        self.lock=RLock()
        self._process_lock=(self.root/'.process.lock').open('a')
        try:
            fcntl.flock(self._process_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self._process_lock.close();raise ConflictError('ARCHIVE_ALREADY_OPEN_IN_ANOTHER_PROCESS') from None
        try:
            self.store=Store(self.root)
            self.reader=reader or DocumentReader(get_tesseract(settings.model_dir),settings)
            self._recover_interrupted()
            self._recover_orphaned_originals()
        except BaseException:
            self.close();raise

    def close(self):
        if not self._process_lock.closed:
            fcntl.flock(self._process_lock,fcntl.LOCK_UN);self._process_lock.close()

    def _recover_interrupted(self):
        with self.store.connect() as connection:
            for row in connection.execute("SELECT id,payload FROM documents WHERE status IN ('received','processing')").fetchall():
                document=Document.model_validate_json(row['payload'])
                for runrow in connection.execute('SELECT id,payload FROM runs WHERE document_id=?',(row['id'],)).fetchall():
                    run=ProcessingRun.model_validate_json(runrow['payload'])
                    if run.status==Status.PROCESSING:
                        failed=run.model_copy(update=dict(status=Status.FAILED,finished_at=now(),errors=('INTERRUPTED_PROCESS',)))
                        connection.execute('UPDATE runs SET payload=? WHERE id=?',(dump(failed),runrow['id']))
                failed=document.model_copy(update=dict(status=Status.FAILED,updated_at=now()))
                connection.execute('UPDATE documents SET status=?,payload=? WHERE id=?',(failed.status,dump(failed),row['id']))

    def _recover_orphaned_originals(self):
        # A power loss between atomic file placement and DB commit must not hide an original.
        import hashlib
        with self.store.connect() as connection:
            for path in sorted((self.root/'originals').iterdir()):
                if path.is_symlink() or not path.is_file() or path.suffix not in ('.pdf','.png','.jpg'):continue
                try:identity=UUID(path.stem)
                except ValueError:continue
                if connection.execute('SELECT 1 FROM documents WHERE id=?',(str(identity),)).fetchone():continue
                digest=hashlib.sha256(path.read_bytes()).hexdigest()
                if connection.execute('SELECT 1 FROM documents WHERE sha256=?',(digest,)).fetchone():continue
                document=Document(id=identity,original_name='recovered-'+path.name,media_type={'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg'}[path.suffix],size_bytes=path.stat().st_size,sha256=digest,original_path='originals/'+path.name,status=Status.FAILED,created_at=now(),updated_at=now())
                run=ProcessingRun(id=uuid4(),document_id=identity,engine='recovery',engine_version='1',settings={},started_at=now(),finished_at=now(),errors=('INTERRUPTED_INGESTION',),status=Status.FAILED)
                connection.execute('INSERT INTO documents VALUES (?,?,?,?,?)',(str(identity),digest,document.status,str(run.id),dump(document)))
                connection.execute('INSERT INTO runs VALUES (?,?,?)',(str(run.id),str(identity),dump(run)))
                self._reindex(connection,identity)

    def _document(self,connection,document_id):
        row=connection.execute('SELECT payload FROM documents WHERE id=?',(str(document_id),)).fetchone()
        if not row:raise NotFoundError('DOCUMENT_NOT_FOUND')
        return Document.model_validate_json(row['payload'])

    def ingest(self,stream,filename):
        with self.lock:
            incoming=receive(stream,filename,self.root,self.settings.max_bytes)
            try:
                with self.store.connect() as connection:
                    found=connection.execute('SELECT id FROM documents WHERE sha256=?',(incoming.sha256,)).fetchone()
                    if found:return dict(document=self._view(connection,found['id']),duplicate=True)
                    identity=uuid4();relative=f'originals/{identity}{incoming.extension}'
                    incoming.temporary.replace(self.root/relative)
                    document=Document(id=identity,original_name=incoming.original_name,media_type=incoming.media_type,size_bytes=incoming.size,sha256=incoming.sha256,original_path=relative,created_at=now(),updated_at=now())
                    connection.execute('INSERT INTO documents(id,sha256,status,payload) VALUES (?,?,?,?)',(str(identity),incoming.sha256,document.status,dump(document)))
                self._process(identity)
                return dict(document=self.get(identity),duplicate=False)
            finally:incoming.temporary.unlink(missing_ok=True)

    def retry(self,document_id):
        with self.lock:
            self._process(document_id)
            return self.get(document_id)

    def _analyze(self,connection,pages,document_id,run_id):
        classification=classify(pages,document_id,run_id)
        connection.execute('INSERT INTO classifications VALUES (?,?)',(str(run_id),dump(classification)))
        self._ensure_fields(connection,pages,classification.category,document_id,run_id)

    def _ensure_fields(self,connection,pages,category,document_id,run_id):
        if connection.execute('SELECT 1 FROM fields WHERE run_id=? AND category=?',(str(run_id),str(category))).fetchone():return
        for field in extract(pages,category,document_id,run_id):
            connection.execute('INSERT INTO fields VALUES (?,?,?,?)',(str(field.id),str(run_id),str(category),dump(field)))

    def _process(self,document_id):
        with self.store.connect() as connection:
            document=self._document(connection,document_id)
            run_id=uuid4()
            run=ProcessingRun(id=run_id,document_id=document.id,engine='pypdf+tesserocr-review-required',engine_version=getattr(self.reader.engine,'version','test-engine'),model_version='tessdata_fast:87416418657359cb625c412a48b6e1d6d41c29bd',settings=dict(max_pages=self.settings.max_pages,max_bytes=self.settings.max_bytes,workers=1,ocr_policy='mandatory-human-review'),started_at=now())
            connection.execute('INSERT INTO runs VALUES (?,?,?)',(str(run_id),str(document.id),dump(run)))
            document=document.model_copy(update=dict(status=Status.PROCESSING,updated_at=now()))
            connection.execute('UPDATE documents SET status=?,current_run_id=?,payload=? WHERE id=?',(document.status,str(run_id),dump(document),str(document.id)))
        start=perf_counter()
        try:
            original=verified_original(self.root,document.original_path,document.sha256)
            result=self.reader.read(original,document_id=document.id,run_id=run_id)
            with self.store.connect() as connection:
                for page in result.pages:
                    connection.execute('INSERT INTO pages VALUES (?,?,?)',(str(run_id),page.number,dump(page)))
                self._analyze(connection,result.pages,document.id,run_id)
                document=document.model_copy(update=dict(page_count=len(result.pages),status=Status.NEEDS_REVIEW,updated_at=now()))
                run=run.model_copy(update=dict(status=Status.NEEDS_REVIEW,finished_at=now(),timings_seconds={**result.timings_seconds,'processing':perf_counter()-start}))
                connection.execute('UPDATE runs SET payload=? WHERE id=?',(dump(run),str(run_id)))
                connection.execute('UPDATE documents SET status=?,payload=? WHERE id=?',(document.status,dump(document),str(document.id)))
                self._reindex(connection,document.id)
        except Exception as error:
            code=str(error) if isinstance(error,(ReadingError,IntegrityError)) else 'PROCESSING_FAILED'
            with self.store.connect() as connection:
                run=run.model_copy(update=dict(status=Status.FAILED,finished_at=now(),errors=(code,),timings_seconds={'processing':perf_counter()-start}))
                document=document.model_copy(update=dict(status=Status.FAILED,updated_at=now()))
                connection.execute('UPDATE runs SET payload=? WHERE id=?',(dump(run),str(run_id)))
                connection.execute('UPDATE documents SET status=?,payload=? WHERE id=?',(document.status,dump(document),str(document.id)))
                self._reindex(connection,document.id)

    def get(self,document_id):
        with self.lock,self.store.connect() as connection:return self._view(connection,document_id)

    def _view(self,connection,document_id):
        document=self._document(connection,document_id)
        row=connection.execute('SELECT current_run_id FROM documents WHERE id=?',(str(document.id),)).fetchone()
        run_id=row['current_run_id']
        runs=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM runs WHERE document_id=? ORDER BY rowid',(str(document.id),))]
        pages=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM pages WHERE run_id=? ORDER BY number',(run_id,))]
        classification_row=connection.execute('SELECT payload FROM classifications WHERE run_id=?',(run_id,)).fetchone()
        engine_classification=json.loads(classification_row['payload']) if classification_row else None
        classification=engine_classification
        reviews=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM reviews WHERE document_id=? AND run_id=? ORDER BY sequence',(str(document.id),run_id))]
        for review in reviews:
            if review['target']=='classification':
                classification=dict(document_id=str(document.id),run_id=run_id,category=review['corrected_value'],method='manual_review',reasons=['user_correction'],confidence=None)
        category=(classification or {}).get('category','unknown')
        fields=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM fields WHERE run_id=? AND category=? ORDER BY rowid',(run_id,category))]
        engine_category=(engine_classification or {}).get('category','unknown')
        engine_fields=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM fields WHERE run_id=? AND category=? ORDER BY rowid',(run_id,engine_category))]
        latest_fields={r['field_id']:r for r in reviews if r['target']=='field'}
        latest_pages={r['page_number']:r for r in reviews if r['target']=='page'}
        for page in pages:
            review=latest_pages.get(page['number'])
            page['effective_text']=review['corrected_value'] if review else page['text']
            page['reviewed']=review is not None
        for field in fields:
            review=latest_fields.get(field['id'])
            check=validate_value(field['name'],review['corrected_value'],review['calendar']) if review else None
            field.update(effective_value=check.normalized if check else field['normalized_value'],effective_status=check.status if check else field['status'],
                         effective_calendar=check.calendar if check else field['calendar'],reviewed=review is not None,review_id=review['id'] if review else None,
                         effective_raw=review['corrected_value'] if review else field['raw_value'])
        history=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM reviews WHERE document_id=? ORDER BY sequence',(str(document.id),))]
        return dict(**document.model_dump(mode='json'),current_run_id=run_id,runs=runs,pages=pages,classification=classification,engine_classification=engine_classification,fields=fields,engine_fields=engine_fields,reviews=history)

    def _reindex(self,connection,document_id):
        view=self._view(connection,document_id)
        content='\n'.join([view['original_name'],*(p['effective_text'] for p in view['pages']),*(str(f['effective_value'] or f['effective_raw'] or '') for f in view['fields'])])
        connection.execute('DELETE FROM document_fts WHERE document_id=?',(str(document_id),))
        connection.execute('INSERT INTO document_fts VALUES (?,?)',(str(document_id),normalize(content)))

    def search(self,query='',category=None,status=None,date_from=None,date_to=None,limit=50,offset=0):
        with self.lock,self.store.connect() as connection:
            sql='SELECT d.id FROM documents d';values=[]
            if query.strip():
                match=literal_query(query)
                if not match:return []
                sql+=' JOIN document_fts f ON f.document_id=d.id WHERE document_fts MATCH ?';values.append(match)
            else:sql+=' WHERE 1=1'
            if status:sql+=' AND d.status=?';values.append(status)
            sql+=' ORDER BY d.rowid DESC'
            result=[]
            for row in connection.execute(sql,values):
                view=self._view(connection,row['id'])
                if category and (view['classification'] or {}).get('category')!=category:continue
                dates=[f for f in view['fields'] if f['name']=='date' and f['effective_calendar']=='gregorian' and f['effective_status']=='valid']
                value=dates[0]['effective_value'] if dates else None
                if date_from and (not value or value<date_from):continue
                if date_to and (not value or value>date_to):continue
                result.append(view)
            return result[offset:offset+limit]

    def original(self,document_id):
        with self.lock,self.store.connect() as connection:
            document=self._document(connection,document_id)
            return verified_original(self.root,document.original_path,document.sha256),document

    def review(self,document_id,target,value,*,field_id=None,page_number=None,calendar='unknown',note=None,expected_run_id=None):
        with self.lock,self.store.connect() as connection:
            view=self._view(connection,document_id)
            if view['status'] in ('failed','processing','received'):raise ConflictError('DOCUMENT_NOT_REVIEWABLE')
            if expected_run_id is not None and str(expected_run_id)!=view['current_run_id']:raise ConflictError('STALE_PROCESSING_RUN')
            if target=='classification':
                try:category=DocumentType(value)
                except ValueError:raise ConflictError('UNSUPPORTED_CATEGORY') from None
                self._ensure_fields(connection,[Page.model_validate({k:v for k,v in p.items() if k not in ('effective_text','reviewed')}) for p in view['pages']],category,UUID(view['id']),UUID(view['current_run_id']))
            elif target=='field':
                field=next((f for f in view['fields'] if f['id']==str(field_id)),None)
                if not field:raise ConflictError('FIELD_NOT_IN_CURRENT_CLASSIFICATION')
                if value is None and not (note or '').strip():raise ConflictError('MISSING_VALUE_REQUIRES_NOTE')
                check=validate_value(field['name'],value,calendar)
                if check.status in ('invalid','ambiguous'):raise ConflictError('CORRECTION_INVALID_OR_AMBIGUOUS')
            elif target=='page':
                if value is None or not value.strip() or not any(p['number']==page_number for p in view['pages']):raise ConflictError('INVALID_PAGE_CORRECTION')
            else:raise ConflictError('UNSUPPORTED_REVIEW_TARGET')
            review=Review(id=uuid4(),document_id=UUID(view['id']),run_id=UUID(view['current_run_id']),target=target,field_id=field_id,page_number=page_number,corrected_value=value,calendar=calendar,note=note,created_at=now())
            connection.execute('INSERT INTO reviews(id,document_id,run_id,payload) VALUES (?,?,?,?)',(str(review.id),view['id'],view['current_run_id'],dump(review)))
            self._set_status(connection,document_id,Status.NEEDS_REVIEW)
            self._reindex(connection,document_id)
            return self._view(connection,document_id)

    def _set_status(self,connection,document_id,status):
        document=self._document(connection,document_id).model_copy(update=dict(status=status,updated_at=now()))
        connection.execute('UPDATE documents SET status=?,payload=? WHERE id=?',(status,dump(document),str(document_id)))

    def confirm(self,document_id,expected_run_id):
        with self.lock,self.store.connect() as connection:
            view=self._view(connection,document_id)
            if str(expected_run_id)!=view['current_run_id']:raise ConflictError('STALE_PROCESSING_RUN')
            if view['status'] not in ('needs_review','completed'):raise ConflictError('DOCUMENT_NOT_REVIEWABLE')
            if not view['pages'] or any(not p['effective_text'].strip() for p in view['pages']):raise ConflictError('EMPTY_TEXT_REQUIRES_REVIEW')
            if any(p['source']=='ocr' and not p['reviewed'] for p in view['pages']):raise ConflictError('OCR_PAGES_MUST_BE_REVIEWED')
            for field in view['fields']:
                if field['effective_status']!='valid' and not (field['effective_status']=='missing' and field['reviewed']):
                    raise ConflictError('UNRESOLVED_FIELDS')
            # Edits to page text require every field to be checked after the latest edit.
            previous_text={p['number']:p['text'] for p in view['pages']}
            changes=[]
            for event in view['reviews']:
                if event['run_id']!=view['current_run_id'] or event['target']!='page':continue
                if previous_text[event['page_number']]!=event['corrected_value']:changes.append(event)
                previous_text[event['page_number']]=event['corrected_value']
            if changes:
                newest=max(r['created_at'] for r in changes)
                for field in view['fields']:
                    relevant=[r for r in view['reviews'] if r['id']==field['review_id']]
                    if not relevant or relevant[-1]['created_at']<newest:raise ConflictError('RECHECK_FIELDS_AFTER_TEXT_EDIT')
            review=Review(id=uuid4(),document_id=UUID(view['id']),run_id=UUID(view['current_run_id']),target='confirmation',corrected_value='confirmed',created_at=now())
            connection.execute('INSERT INTO reviews(id,document_id,run_id,payload) VALUES (?,?,?,?)',(str(review.id),view['id'],view['current_run_id'],dump(review)))
            self._set_status(connection,document_id,Status.COMPLETED)
            return self._view(connection,document_id)

    def run_detail(self,document_id,run_id):
        with self.lock,self.store.connect() as connection:
            self._document(connection,document_id)
            row=connection.execute('SELECT payload FROM runs WHERE id=? AND document_id=?',(str(run_id),str(document_id))).fetchone()
            if not row:raise NotFoundError('RUN_NOT_FOUND')
            classification=connection.execute('SELECT payload FROM classifications WHERE run_id=?',(str(run_id),)).fetchone()
            return dict(run=json.loads(row['payload']),classification=json.loads(classification['payload']) if classification else None,
                        pages=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM pages WHERE run_id=? ORDER BY number',(str(run_id),))],
                        fields=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM fields WHERE run_id=? ORDER BY rowid',(str(run_id),))],
                        reviews=[json.loads(r['payload']) for r in connection.execute('SELECT payload FROM reviews WHERE document_id=? AND run_id=? ORDER BY sequence',(str(document_id),str(run_id)))])

    def preview(self,document_id,page_number=1):
        # A locally rendered PNG avoids dependence on browser PDF plug-ins.
        from io import BytesIO
        from PIL import Image, ImageOps
        import pypdfium2 as pdfium
        with self.lock:
            path,document=self.original(document_id)
            try:
                if document.media_type=='application/pdf':
                    pdf=pdfium.PdfDocument(path)
                    try:
                        if not 1<=page_number<=min(len(pdf),self.settings.max_pages):raise IntegrityError('PREVIEW_UNAVAILABLE')
                        page=pdf[page_number-1]
                        try:
                            width,height=page.get_size()
                            if width*height*(120/72)**2>20_000_000:raise IntegrityError('PREVIEW_UNAVAILABLE')
                            bitmap=page.render(scale=120/72)
                            try:image=bitmap.to_pil().convert('RGB')
                            finally:bitmap.close()
                        finally:page.close()
                    finally:pdf.close()
                else:
                    if page_number!=1:raise IntegrityError('PREVIEW_UNAVAILABLE')
                    with Image.open(path) as original:
                        if original.width*original.height>20_000_000:raise IntegrityError('PREVIEW_UNAVAILABLE')
                        image=ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((1400,2000))
                output=BytesIO();image.save(output,format='PNG');return output.getvalue()
            except IntegrityError:raise
            except Exception as error:raise IntegrityError('PREVIEW_UNAVAILABLE') from error
