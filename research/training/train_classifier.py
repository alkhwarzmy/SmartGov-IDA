"""Train a bounded text classifier on an explicit licensed, real-document manifest."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src')]
from archive_system.classification.rules import classify
from archive_system.domain.models import Page
from archive_system.search.text import normalize

LABELS=('letter','invoice','unknown')
ALLOWED_LICENSES={'CC0-1.0','CC-BY-4.0','CC-BY-SA-4.0','CC-BY-NC-SA-4.0','Public-Domain','authorized-private'}


def load_real_manifest(path: Path,accept_noncommercial=False):
    manifest=json.loads(path.read_text());records=manifest.get('records',[])
    if manifest.get('kind')!='real' or not records:raise ValueError('REAL_DATA_MANIFEST_REQUIRED')
    if len(records)>200:raise ValueError('INITIAL_200_DOCUMENT_LIMIT')
    groups={s:set() for s in ('train','validation','test')};hashes={s:set() for s in groups};texts=[]
    for record in records:
        if record['label'] not in LABELS or record['split'] not in groups:raise ValueError('UNSUPPORTED_LABEL_OR_SPLIT')
        if not record.get('group') or not record.get('source') or record.get('license_scope')!='data':raise ValueError('DATA_PROVENANCE_REQUIRED')
        license=record.get('license')
        if license not in ALLOWED_LICENSES:raise ValueError('UNREVIEWED_DATA_LICENSE')
        if license=='CC-BY-NC-SA-4.0' and not accept_noncommercial:raise ValueError('NONCOMMERCIAL_ACCEPTANCE_REQUIRED')
        if license=='authorized-private' and not record.get('authorization_reference'):raise ValueError('EXPLICIT_TRAINING_AUTHORIZATION_REQUIRED')
        if license!='authorized-private' and not record.get('license_reference'):raise ValueError('DATA_LICENSE_REFERENCE_REQUIRED')
        text_path=(path.parent/record['text_path']).resolve()
        if not text_path.is_relative_to(path.parent.resolve()):raise ValueError('UNSAFE_TEXT_PATH')
        if text_path.stat().st_size>2*1024**2:raise ValueError('TEXT_FILE_LIMIT')
        data=text_path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=record['sha256']:raise ValueError('TEXT_CHECKSUM_MISMATCH')
        text=data.decode('utf-8')
        if not normalize(text):raise ValueError('EMPTY_TEXT')
        canonical=hashlib.sha256(normalize(text).encode()).hexdigest()
        groups[record['split']].add(record['group']);hashes[record['split']].add(canonical);texts.append(text)
    for a,b in [('train','validation'),('train','test'),('validation','test')]:
        if groups[a]&groups[b] or hashes[a]&hashes[b]:raise ValueError('DOCUMENT_OR_TEMPLATE_LEAKAGE')
    for split,minimum in [('train',5),('validation',2),('test',2)]:
        counts=Counter(r['label'] for r in records if r['split']==split)
        if any(counts[label]<minimum for label in LABELS):raise ValueError('INSUFFICIENT_REAL_SAMPLES_PER_CLASS')
    return records,texts


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--accept-noncommercial',action='store_true')
    args=parser.parse_args();manifest=args.manifest.resolve();output=args.output.resolve()
    if manifest.is_relative_to(ROOT) or output.is_relative_to(ROOT):raise SystemExit('Real data and weights must remain outside Git')
    if output.exists():raise SystemExit('Select a new run directory')
    records,texts=load_real_manifest(manifest,args.accept_noncommercial)
    import sklearn
    from sklearn.pipeline import make_pipeline
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import classification_report,confusion_matrix
    import joblib
    import psutil
    model=make_pipeline(TfidfVectorizer(preprocessor=normalize,analyzer='char',ngram_range=(3,5),min_df=2,max_features=20000,sublinear_tf=True),
                        LogisticRegression(C=1.0,class_weight='balanced',max_iter=1000,random_state=42,n_jobs=1))
    train=[i for i,r in enumerate(records) if r['split']=='train']
    start=perf_counter();model.fit([texts[i] for i in train],[records[i]['label'] for i in train]);seconds=perf_counter()-start
    evaluation={}
    for split in ('validation','test'):
        indexes=[i for i,r in enumerate(records) if r['split']==split];truth=[records[i]['label'] for i in indexes]
        predictions=model.predict([texts[i] for i in indexes]).tolist()
        baseline=[]
        for i in indexes:
            document_id=uuid4();run_id=uuid4();page=Page(document_id=document_id,run_id=run_id,number=1,text=texts[i],source='digital')
            baseline.append(classify([page],document_id,run_id).category)
        evaluation[split]=dict(samples=len(indexes),candidate=classification_report(truth,predictions,labels=list(LABELS),output_dict=True,zero_division=0),
                               baseline_rules=classification_report(truth,baseline,labels=list(LABELS),output_dict=True,zero_division=0),
                               candidate_confusion=confusion_matrix(truth,predictions,labels=list(LABELS)).tolist(),label_order=LABELS)
    output.mkdir(parents=True);model_path=output/'classifier.joblib';joblib.dump(model,model_path)
    result=dict(created_at=datetime.now(timezone.utc).isoformat(),trained_on_real_data=True,default_application_changed=False,sklearn_version=sklearn.__version__,
                manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
                model_path=str(model_path),train_samples=len(train),train_seconds=seconds,rss_after_training_bytes=psutil.Process().memory_info().rss,
                parameters=dict(model='char-TFIDF+balanced-logistic-regression',ngrams=[3,5],features=20000,C=1.0,random_state=42),evaluation=evaluation,
                validation_improved=evaluation['validation']['candidate']['macro avg']['f1-score']>evaluation['validation']['baseline_rules']['macro avg']['f1-score'],
                limitations=['No automatic deployment; the independent test must be interpreted at the document/template/source level','A text-transcription benchmark does not establish image-OCR classification accuracy','Load only this locally generated joblib artifact with the recorded SHA-256; pickle files from untrusted sources must never be loaded'])
    reports=ROOT/'evaluation/research';reports.mkdir(parents=True,exist_ok=True)
    (reports/'classifier-training.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:result[key] for key in ['train_samples','train_seconds','validation_improved','model_sha256']}))

if __name__=='__main__':main()
