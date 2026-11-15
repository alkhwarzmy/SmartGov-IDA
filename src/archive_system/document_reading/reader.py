from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import UUID
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
import pypdfium2 as pdfium
from archive_system.config import Settings
from archive_system.domain.models import Page
from archive_system.ocr.base import OCREngine
from .quality import assess_text

class ReadingError(ValueError):
    """Stable code, deliberately excludes document text."""

@dataclass(frozen=True)
class ReadResult:
    pages: tuple[Page, ...]
    quality_reasons: dict[int, tuple[str, ...]]
    timings_seconds: dict[str, float]

class DocumentReader:
    def __init__(self, engine: OCREngine, settings: Settings | None = None, *, dpi: int = 200, max_pixels: int = 20_000_000):
        if not 72 <= dpi <= 300 or max_pixels < 1:
            raise ValueError('INVALID_RENDER_SETTINGS')
        self.engine, self.settings = engine, settings or Settings()
        self.dpi, self.max_pixels = dpi, max_pixels

    def read(self, path: Path, *, document_id: UUID, run_id: UUID) -> ReadResult:
        size = path.stat().st_size
        if size == 0:
            raise ReadingError('EMPTY_FILE')
        if size > self.settings.max_bytes:
            raise ReadingError('FILE_TOO_LARGE')
        start = perf_counter()
        with path.open('rb') as stream:
            signature = stream.read(8)
        if signature.startswith(b'%PDF-'):
            return self._pdf(path, document_id, run_id, start)
        if not (signature.startswith(b'\x89PNG\r\n\x1a\n') or signature.startswith(b'\xff\xd8\xff')):
            raise ReadingError('UNSUPPORTED_OR_CORRUPT_FILE')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if image.width * image.height > self.max_pixels:
                        raise ReadingError('IMAGE_PIXEL_LIMIT')
                    if getattr(image, 'n_frames', 1) != 1:
                        raise ReadingError('MULTIFRAME_IMAGE_UNSUPPORTED')
                    image.load()
                    oriented = ImageOps.exif_transpose(image).convert('RGB')
            read_time = perf_counter() - start
            ocr_start = perf_counter()
            result = self.engine.recognize(oriented)
            return ReadResult((Page(document_id=document_id,run_id=run_id,number=1,text=result.text,source='ocr',regions=result.regions,confidence=result.confidence),),
                              {1: ('image_input',) + (() if result.text.strip() else ('ocr_empty',))}, {'reading':read_time,'ocr':perf_counter()-ocr_start,'total':perf_counter()-start})
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ReadingError('CORRUPT_OR_OVERSIZED_IMAGE') from exc

    def _pdf(self, path, document_id, run_id, start):
        try:
            pdf = PdfReader(path, strict=True)
            if pdf.is_encrypted:
                raise ReadingError('ENCRYPTED_PDF')
            count = len(pdf.pages)
            if count == 0:
                raise ReadingError('EMPTY_PDF')
            if count > self.settings.max_pages:
                raise ReadingError('PAGE_LIMIT_EXCEEDED')
            pages, reasons = [], {}
            render_time = ocr_time = extraction_time = 0.0
            rendered = None
            try:
                for number, page in enumerate(pdf.pages, 1):
                    t = perf_counter()
                    chunks = []
                    try:
                        text = page.extract_text(visitor_text=lambda text, *_: chunks.append(text)) or ''
                        # Inspect resources without decompressing embedded image pixels.
                        resources = page.get('/Resources', {})
                        resources = resources.get_object() if hasattr(resources, 'get_object') else resources
                        objects = resources.get('/XObject', {})
                        objects = objects.get_object() if hasattr(objects, 'get_object') else objects
                        has_image = any(v.get_object().get('/Subtype') in ('/Image','/Form') for v in objects.values())
                        quality = assess_text(text, has_image=has_image, visitor_text=''.join(chunks))
                    except Exception:
                        text = ''; quality = assess_text('')
                    extraction_time += perf_counter()-t
                    if quality.usable:
                        pages.append(Page(document_id=document_id,run_id=run_id,number=number,text=text,source='digital'))
                        continue
                    reasons[number] = quality.reasons
                    t = perf_counter()
                    if rendered is None:
                        rendered = pdfium.PdfDocument(path)
                    render_page = rendered[number-1]
                    try:
                        width, height = render_page.get_size()
                        if width * height * (self.dpi/72)**2 > self.max_pixels:
                            raise ReadingError('PDF_RENDER_PIXEL_LIMIT')
                        bitmap = render_page.render(scale=self.dpi/72)
                        try:
                            image = bitmap.to_pil().convert('RGB')
                        finally:
                            bitmap.close()
                    finally:
                        render_page.close()
                    render_time += perf_counter()-t
                    t = perf_counter()
                    result = self.engine.recognize(image)
                    ocr_time += perf_counter()-t
                    if not result.text.strip():
                        reasons[number] += ('ocr_empty',)
                    pages.append(Page(document_id=document_id,run_id=run_id,number=number,text=result.text,source='ocr',regions=result.regions,confidence=result.confidence))
            finally:
                if rendered is not None:
                    rendered.close()
                pdf.close()
            return ReadResult(tuple(pages),reasons,{'digital_extraction':extraction_time,'render':render_time,'ocr':ocr_time,'total':perf_counter()-start})
        except ReadingError:
            raise
        except Exception as exc:
            raise ReadingError('PDF_READING_FAILED') from exc
