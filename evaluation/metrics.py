"""Micro-averaged Levenshtein CER/WER, never clipped at 100%."""
import re
import unicodedata

_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789')

def normalize(text: str) -> str:
    text = unicodedata.normalize('NFC', text).translate(_DIGITS)
    text = re.sub('[\u064b-\u065f\u0670\u0640]', '', text)
    return ' '.join(text.split())

def distance(reference, hypothesis):
    previous = list(range(len(hypothesis)+1))
    for i,a in enumerate(reference,1):
        current=[i]
        for j,b in enumerate(hypothesis,1):
            current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
        previous=current
    return previous[-1]

def counts(reference: str, hypothesis: str) -> dict:
    result = {}
    for label, ref, hyp in [('raw',reference,hypothesis),('normalized',normalize(reference),normalize(hypothesis))]:
        result[label] = dict(char_errors=distance(ref,hyp),characters=len(ref),word_errors=distance(ref.split(),hyp.split()),words=len(ref.split()))
    return result

def aggregate(rows):
    output={}
    for label in ('raw','normalized'):
        sums={key:sum(r[label][key] for r in rows) for key in ('char_errors','characters','word_errors','words')}
        output[label]={**sums,'cer':sums['char_errors']/sums['characters'] if sums['characters'] else None,'wer':sums['word_errors']/sums['words'] if sums['words'] else None}
    return output
