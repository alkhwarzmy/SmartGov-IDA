"""Evaluate frozen held-out documents once rules are frozen. No rule tuning here."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from threading import Event,Thread
from time import perf_counter
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from archive_system.application.service import ArchiveService
from archive_system.config import Settings
from archive_system.domain.models import Page
from archive_system.classification.rules import classify
from archive_system.extraction.rules import extract
from evaluation.quality import classification_metrics,field_metrics
from evaluation.metrics import counts,aggregate
import psutil


def evaluate():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--corpus',type=Path,default=Path('/workspace/smartgov-corpus/v1'));parser.add_argument('--models',type=Path,default=Path('/workspace/smartgov-models/tesseract'))
    parser.add_argument('--out',type=Path,default=ROOT/'evaluation/results/mvp-regression.json')
    args=parser.parse_args()
    if args.out.exists():raise SystemExit('Evaluation output exists; choose a new path to preserve previous evidence')
    ground=json.loads((ROOT/'evaluation/ground_truth.json').read_text());test=[g for g in ground if g['split']=='test']
    assert hashlib.sha256(json.dumps(test,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==(ROOT/'evaluation/test_annotations.sha256').read_text().strip()
    code_paths=[*sorted((ROOT/'src/archive_system/classification').glob('*.py')),*sorted((ROOT/'src/archive_system/extraction').glob('*.py')),*sorted((ROOT/'src/archive_system/validation').glob('*.py'))]
    code_hashes={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in code_paths}
    reference_rows=[];digital_rows=[];ocr_rows=[];reading=[]
    proc=psutil.Process();peak=[proc.memory_info().rss];stop=Event()
    def sample():
        while not stop.wait(.01):peak[0]=max(peak[0],proc.memory_info().rss)
    monitor=Thread(target=sample,daemon=True);monitor.start()
    def row(item,category,fields):return dict(document=item['id'],truth_category=item['category'],predicted_category=category,truth_fields=item['fields'],predicted_fields=fields)
    try:
        with tempfile.TemporaryDirectory(prefix='smartgov-evaluation-') as folder:
            start=perf_counter();service=ArchiveService(Settings(runtime_dir=Path(folder),model_dir=args.models));startup=perf_counter()-start
            try:
                for item in test:
                    page=Page(document_id=uuid4(),run_id=uuid4(),number=1,text=item['reference_text'],source='digital')
                    category=classify([page],page.document_id,page.run_id).category
                    fields=[f.model_dump(mode='json') for f in extract([page],category,page.document_id,page.run_id)]
                    reference_rows.append(row(item,category,fields))
                    for variant,suffix in [('digital','-digital.pdf'),('ocr','.png')]:
                        path=args.corpus/(item['id']+suffix)
                        with path.open('rb') as stream:result=service.ingest(stream,path.name)['document']
                        assert result['status']=='needs_review',result['runs'][-1]['errors']
                        target=digital_rows if variant=='digital' else ocr_rows
                        target.append(row(item,result['engine_classification']['category'],result['engine_fields']))
                        text='\n'.join(p['text'] for p in result['pages'])
                        reading.append(dict(document=item['id'],variant=variant,source=[p['source'] for p in result['pages']],text_metrics=counts(item['reference_text'],text),timings_seconds=result['runs'][-1]['timings_seconds']))
                storage_bytes=sum(p.stat().st_size for p in Path(folder).rglob('*') if p.is_file())
            finally:service.close()
    finally:stop.set();monitor.join()
    report=dict(evaluation_role='post-diagnosis-regression-not-independent',created_at=datetime.now(timezone.utc).isoformat(),test_documents=len(test),reference_documents=len(reference_rows),digital_cases=len(digital_rows),ocr_cases=len(ocr_rows),
                code_sha256=code_hashes,held_out_annotations_unchanged=True,reference_text=dict(classification=classification_metrics(reference_rows),extraction=field_metrics(reference_rows)),
                digital=dict(classification=classification_metrics(digital_rows),extraction=field_metrics(digital_rows),text=aggregate([r['text_metrics'] for r in reading if r['variant']=='digital'])),
                ocr=dict(classification=classification_metrics(ocr_rows),extraction=field_metrics(ocr_rows),text=aggregate([r['text_metrics'] for r in reading if r['variant']=='ocr'])),
                startup_seconds=startup,peak_rss_bytes=peak[0],memory_sampling_seconds=.01,storage_bytes=storage_bytes,platform=platform.platform(),python=platform.python_version(),logical_cpus=os.cpu_count(),cpu_threads=os.environ.get('OMP_NUM_THREADS','not_pinned'),
                model_bytes=sum(p.stat().st_size for p in args.models.glob('*.traineddata')),reading=reading,
                limitations=['Synthetic data only; 8 held-out documents, one author, two template families per supported type across splits','Tesseract is an assistive OCR candidate, not an accuracy-approved production engine','Paddle comparison blocked; no claims of engine superiority','All archived documents require explicit review; date calendar is never inferred from ISO-shaped digits alone'])
    output=args.out;output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('test_documents','startup_seconds','peak_rss_bytes','storage_bytes')}))
    for key in ('reference_text','digital','ocr'):print(key,json.dumps(report[key],ensure_ascii=False))

if __name__=='__main__':evaluate()
