"""Anchored label extraction: no guessing from unrelated numbers or dates."""
import re
from uuid import uuid4
from archive_system.domain.models import ExtractedField, Source, FIELD_NAMES, DocumentType, ValidationStatus as V
from archive_system.validation.fields import clean, validate_value

ALIASES={
 'letter_number':('رقم الخطاب','letter number'),
 'invoice_number':('رقم الفاتورة','invoice number','invoice no'),
 'date':('التاريخ','date'),
 'sender':('المرسل','الجهة المرسلة','from'),
 'recipient':('المستقبل','الجهة المستقبلة','إلى','to'),
 'subject':('الموضوع','subject'),
 'supplier':('المورد','البائع','supplier','vendor'),
 'total':('الإجمالي','الاجمالي','المجموع الكلي','total'),
 'currency':('العملة','currency'),
}

def extract(pages,category,document_id,run_id):
    fields=[]
    for name in FIELD_NAMES[DocumentType(category)]:
        candidates=[]
        layout_only=False
        aliases={clean(label).casefold() for label in ALIASES[name]}
        for page in pages:
            for line in page.text.splitlines():
                parts=re.split(r'\s*[:：—–]\s*',line,maxsplit=1)
                if len(parts)==2 and clean(parts[0]).casefold() in aliases:
                    candidates.append((parts[1],Source(page=page.number,snippet=line)))
                else:
                    # Some PDF generators lose the separator glyph. An anchored label
                    # plus a layout gap yields a candidate, always requiring review.
                    gap=re.split(r'\s{2,}',line.strip(),maxsplit=1)
                    if len(gap)==2 and clean(gap[0]).casefold() in aliases:
                        candidates.append((gap[1],Source(page=page.number,snippet=line)))
                        layout_only=True
        distinct={clean(raw) for raw,_ in candidates}
        if len(distinct)>1:
            value=' / '.join(raw for raw,_ in candidates)
            check=validate_value(name,value)
            status=V.AMBIGUOUS;normalized=None;reason='conflicting_labeled_values';calendar='unknown'
        elif candidates:
            value=candidates[0][0];check=validate_value(name,value)
            status,normalized,reason,calendar=check.status,check.normalized,check.reason,check.calendar
            if layout_only and status==V.VALID:
                status=V.NEEDS_REVIEW;reason='layout_gap_without_separator'
        else:
            value=None;status=V.MISSING;normalized=None;reason='absent';calendar='unknown'
        fields.append(ExtractedField(id=uuid4(),document_id=document_id,run_id=run_id,name=name,raw_value=value,normalized_value=normalized,
                                     sources=tuple(source for _,source in candidates),status=status,reason=reason,calendar=calendar))
    return fields
