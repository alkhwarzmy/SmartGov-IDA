from pathlib import Path
from uuid import uuid4
import pytest
from archive_system.document_reading.quality import assess_text
from archive_system.ocr.tesseract import TesseractEngine, MissingModelError
from archive_system.domain.models import Page
from evaluation.metrics import counts, aggregate, normalize, distance

@pytest.mark.parametrize('text,reason', [('', 'too_short'),('ا'*50,'repetitive_text'),('\ufffd'*30,'invalid_unicode'),('!%$'*30,'low_text_ratio')])
def test_rejects_bad_layers(text,reason):
    result=assess_text(text)
    assert not result.usable
    assert reason in result.reasons

def test_mixed_direction_loss_is_detected():
    text='خطاب إداري ورقم الخطاب والتاريخ والموضوع'
    result=assess_text(text,visitor_text=text+' L-101 2026-11-15')
    assert result.reasons==('lost_mixed_direction_runs',)

def test_sparse_overlay_is_not_sufficient():
    text='خطاب إداري عن طلب مستلزمات مكتبية'
    assert assess_text(text).usable
    assert not assess_text(text,has_image=True).usable

def test_model_missing_fails_without_downloading(tmp_path):
    with pytest.raises(MissingModelError,match='LOCAL_MODEL_MISSING_OR_CHANGED'):
        TesseractEngine(tmp_path)

def test_model_checksum_change_rejected(tmp_path):
    (tmp_path/'ara.traineddata').write_bytes(b'changed')
    with pytest.raises(MissingModelError):TesseractEngine(tmp_path)

def test_metrics_counts_insertions_deletions_and_substitutions():
    assert distance('abc','axc!')==2
    assert distance('','abc')==3
    assert distance('abc','')==3
    result=counts('one two','one three')
    assert result['raw']['word_errors']==1
    assert aggregate([result])['raw']['wer']==0.5
    assert counts('a','aaaa')['raw']['char_errors']==3

def test_normalization_preserves_calendar_and_letters():
    assert normalize('  إِدَارَة  ١٢ـ٣  ۴۵  ')=='إدارة 123 45'
    assert normalize('2026-11-15')=='2026-11-15'
    assert normalize('أ إ ا')=='أ إ ا'
    assert aggregate([counts('','')])['raw']['cer'] is None
