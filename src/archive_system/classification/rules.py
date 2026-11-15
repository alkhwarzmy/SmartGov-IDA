"""Explainable document rules; no pseudo-probability from keyword counts."""
import re
from archive_system.domain.models import Classification, DocumentType
from archive_system.search.text import normalize

def classify(pages,document_id,run_id):
    text=normalize('\n'.join(page.text for page in pages))
    invoice=bool(re.search(r'(?:فاتورة|\binvoice\b)',text))
    letter=bool(re.search(r'(?:خطاب إداري|رقم الخطاب|\badministrative letter\b|\bletter number\b)',text))
    if invoice and not letter:category=DocumentType.INVOICE;reasons=('invoice_marker',)
    elif letter and not invoice:category=DocumentType.LETTER;reasons=('letter_marker',)
    else:category=DocumentType.UNKNOWN;reasons=('conflicting_markers' if invoice and letter else 'no_supported_marker',)
    return Classification(document_id=document_id,run_id=run_id,category=category,method='rules-v1',reasons=reasons)
