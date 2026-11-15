from evaluation.quality import classification_metrics,field_metrics

def test_confusion_matrix_and_macro_f1_includes_unknown():
    result=classification_metrics([{'truth_category':'letter','predicted_category':'invoice'},{'truth_category':'invoice','predicted_category':'invoice'},{'truth_category':'unknown','predicted_category':'unknown'}])
    assert result['confusion_matrix']['letter']['invoice']==1
    assert result['per_class']['invoice']['precision']==0.5
    assert abs(result['macro_f1']-(0+2/3+1)/3)<1e-9

def test_wrong_value_counts_fp_and_fn_and_nulls_are_explicit():
    rows=[dict(truth_category='invoice',predicted_category='invoice',truth_fields=[dict(name='total',raw_value='1',normalized_value='1')],predicted_fields=[dict(name='total',raw_value='2',normalized_value='2')])]
    metric=field_metrics(rows)['per_field']['total']
    assert metric['fp']==1 and metric['fn']==1 and metric['tp']==0 and metric['f1']==0
