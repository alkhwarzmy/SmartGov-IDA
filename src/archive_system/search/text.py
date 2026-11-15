import re
import unicodedata

DIGITS=str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789')

def normalize(text: str) -> str:
    text=unicodedata.normalize('NFC',text).translate(DIGITS)
    text=re.sub('[\u064b-\u065f\u0670\u0640\u200e\u200f]', '',text)
    return ' '.join(text.casefold().split())

def literal_query(text: str) -> str:
    # FTS syntax never comes from the user's input; every term is a quoted literal.
    terms=re.findall(r'[^\W_]+(?:[-./][^\W_]+)*',normalize(text),flags=re.UNICODE)
    return ' AND '.join('"'+term.replace('"','""')+'"' for term in terms[:20])
