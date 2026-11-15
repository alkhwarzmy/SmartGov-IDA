import hashlib
import json
import pytest
from research.training.train_ocr import validate_dataset
from research.training.train_classifier import load_real_manifest


def test_ocr_split_rejects_shared_book_and_duplicate_image():
    records=[dict(split=s,source_group=s,image_sha256=s,text_sha256=s) for s in ('train','validation','test')]
    assert len(validate_dataset(dict(license='CC-BY-NC-SA-4.0',records=records)))==3
    records[2]['source_group']='train'
    with pytest.raises(ValueError,match='LEAKAGE'):validate_dataset(dict(license='CC-BY-NC-SA-4.0',records=records))


def test_classifier_rejects_synthetic_claim_and_code_license(tmp_path):
    path=tmp_path/'manifest.json';path.write_text(json.dumps({'kind':'synthetic','records':[{}]}))
    with pytest.raises(ValueError,match='REAL_DATA'):load_real_manifest(path)
    text=b'Invoice sample for a unit test only';(tmp_path/'text.txt').write_bytes(text)
    record=dict(label='invoice',split='train',group='source-a',source='unit-test',license='MIT',license_scope='code',text_path='text.txt',sha256=hashlib.sha256(text).hexdigest())
    path.write_text(json.dumps(dict(kind='real',records=[record])))
    with pytest.raises(ValueError,match='PROVENANCE'):load_real_manifest(path)


def test_classifier_rejects_template_leakage_before_training(tmp_path):
    records=[]
    for split,text in [('train','first invoice'),('validation','different invoice'),('test','third invoice')]:
        data=text.encode();(tmp_path/(split+'.txt')).write_bytes(data)
        records.append(dict(label='invoice',split=split,group='same-template',source='unit-test',license='CC-BY-4.0',license_scope='data',license_reference='unit-test-license',text_path=split+'.txt',sha256=hashlib.sha256(data).hexdigest()))
    path=tmp_path/'manifest.json';path.write_text(json.dumps(dict(kind='real',records=records)))
    with pytest.raises(ValueError,match='LEAKAGE'):load_real_manifest(path)
