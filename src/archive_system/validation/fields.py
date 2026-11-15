from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from archive_system.domain.models import ValidationStatus as V
from archive_system.search.text import DIGITS

@dataclass(frozen=True)
class ValueCheck:
    normalized: str | None
    status: V
    reason: str | None = None
    calendar: str = 'unknown'

def clean(value):
    value=unicodedata.normalize('NFC',value).translate(DIGITS)
    return ' '.join(re.sub('[\u064b-\u065f\u0670\u0640\u200e\u200f]','',value).split())

def validate_value(name: str, raw: str | None, calendar='unknown') -> ValueCheck:
    if raw is None:return ValueCheck(None,V.MISSING,'absent')
    value=clean(raw)
    if not value or re.search(r'غير مقروء|unreadable|\?{2,}',value,re.I):
        return ValueCheck(None,V.NEEDS_REVIEW,'unreadable_or_empty')
    if re.search(r'\s/\s|\sأو\s|\sor\s',value,re.I):
        return ValueCheck(None,V.AMBIGUOUS,'multiple_candidates')
    if name=='date':
        if re.search(r'هجري|\bAH\b|\sهـ',value,re.I):calendar='hijri'
        if re.search(r'ميلادي|\bAD\b',value,re.I):calendar='gregorian'
        numeric=re.sub(r'\s*(?:ميلادي|هجري|هـ|AD|AH)\s*','',value,flags=re.I)
        match=re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',numeric)
        if not match:return ValueCheck(value,V.NEEDS_REVIEW,'date_order_not_proven',calendar)
        year,month,day=map(int,match.groups())
        normalized=f'{year:04}-{month:02}-{day:02}'
        if calendar=='gregorian':
            try:date(year,month,day)
            except ValueError:return ValueCheck(normalized,V.INVALID,'invalid_gregorian_date',calendar)
        elif calendar=='hijri':
            if not (1<=month<=12 and 1<=day<=30 and year>0):return ValueCheck(normalized,V.INVALID,'invalid_hijri_components',calendar)
            return ValueCheck(normalized,V.NEEDS_REVIEW,'hijri_calendar_not_verified',calendar)
        else:
            if not (1<=month<=12 and 1<=day<=31 and year>0):return ValueCheck(normalized,V.INVALID,'invalid_date_components',calendar)
            return ValueCheck(normalized,V.NEEDS_REVIEW,'calendar_unknown',calendar)
        return ValueCheck(normalized,V.VALID,calendar=calendar)
    if name=='total':
        numeric=value.replace('٬',',').replace('٫','.')
        if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?',numeric):
            return ValueCheck(None,V.INVALID,'invalid_money_format')
        try:amount=Decimal(numeric.replace(',',''))
        except InvalidOperation:return ValueCheck(None,V.INVALID,'invalid_money')
        if not amount.is_finite() or amount<0:return ValueCheck(None,V.INVALID,'invalid_money')
        return ValueCheck(format(amount,'.2f'),V.VALID)
    if name=='currency':
        currencies={'sar':'SAR','ر.س':'SAR','ريال سعودي':'SAR','usd':'USD','دولار أمريكي':'USD','yer':'YER','ريال يمني':'YER','eur':'EUR','يورو':'EUR','aed':'AED','درهم إماراتي':'AED'}
        code=currencies.get(value.casefold())
        return ValueCheck(code,V.VALID) if code else ValueCheck(value,V.NEEDS_REVIEW,'currency_not_explicit_or_supported')
    if name in ('letter_number','invoice_number'):
        if not re.fullmatch(r'[\w/-]{1,60}',value,re.UNICODE) or not any(c.isdigit() for c in value):
            return ValueCheck(value,V.INVALID,'invalid_identifier')
    if len(value)>500:return ValueCheck(value,V.NEEDS_REVIEW,'unusually_long_value')
    return ValueCheck(value,V.VALID)
