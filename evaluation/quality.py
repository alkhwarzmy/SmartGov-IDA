"""Classification and field-value metrics with explicit missing-value accounting."""
from collections import Counter
from evaluation.metrics import normalize

CATEGORIES=('letter','invoice','unknown')

def classification_metrics(rows):
    matrix={truth:{prediction:0 for prediction in CATEGORIES} for truth in CATEGORIES}
    for row in rows:matrix[row['truth_category']][row['predicted_category']]+=1
    per_class={}
    for category in CATEGORIES:
        tp=matrix[category][category]
        fp=sum(matrix[t][category] for t in CATEGORIES if t!=category)
        fn=sum(matrix[category][p] for p in CATEGORIES if p!=category)
        precision=tp/(tp+fp) if tp+fp else 0
        recall=tp/(tp+fn) if tp+fn else 0
        per_class[category]=dict(precision=precision,recall=recall,f1=2*precision*recall/(precision+recall) if precision+recall else 0,support=sum(matrix[category].values()))
    return dict(confusion_matrix=matrix,per_class=per_class,macro_f1=sum(v['f1'] for v in per_class.values())/len(CATEGORIES))

def field_metrics(rows):
    names=sorted({f['name'] for row in rows for f in row['truth_fields']})
    per_field={}
    correct_documents=0;supported=0
    for name in names:
        total=raw_correct=normalized_correct=tp=fp=fn=absent=0
        for row in rows:
            truth=next((f for f in row['truth_fields'] if f['name']==name),None)
            if truth is None:continue
            predicted=next((f for f in row['predicted_fields'] if f['name']==name),{})
            total+=1
            raw_correct+=predicted.get('raw_value')==truth['raw_value']
            expected=truth['normalized_value'];actual=predicted.get('normalized_value')
            match=(normalize(actual) if actual is not None else None)==(normalize(expected) if expected is not None else None)
            normalized_correct+=match;absent+=expected is None
            if expected is not None and actual is not None and match:tp+=1
            else:
                fp+=actual is not None;fn+=expected is not None
        precision=tp/(tp+fp) if tp+fp else None
        recall=tp/(tp+fn) if tp+fn else None
        f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
        per_field[name]=dict(samples=total,truth_unresolved_or_missing=absent,exact_match=raw_correct/total,normalized_exact_match=normalized_correct/total,
                             tp=tp,fp=fp,fn=fn,precision=precision,recall=recall,f1=f1)
    for row in rows:
        if not row['truth_fields']:continue
        supported+=1
        correct=row['truth_category']==row['predicted_category']
        for truth in row['truth_fields']:
            predicted=next((f for f in row['predicted_fields'] if f['name']==truth['name']),{})
            expected=truth['normalized_value'];actual=predicted.get('normalized_value')
            correct &= (normalize(actual) if actual is not None else None)==(normalize(expected) if expected is not None else None)
        correct_documents+=correct
    return dict(per_field=per_field,supported_documents=supported,all_required_values_correct=correct_documents/supported if supported else None,
                note='Exact-value presence F1; a wrong non-null value counts as FP and FN. NEM includes expected nulls, reported separately. Value correctness does not establish review completion or calendar certainty.')
