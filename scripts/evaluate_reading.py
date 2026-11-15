"""Evaluate local reading; predictions contain fictional corpus text only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from threading import Event, Thread
from time import perf_counter
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from evaluation.metrics import counts, aggregate


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--engine',choices=['tesseract'],default='tesseract')
    parser.add_argument('--split',choices=['development','test'],default='development')
    parser.add_argument('--model-dir',type=Path,required=True)
    parser.add_argument('--corpus',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--psm',type=int,choices=[3,6],default=6)
    args=parser.parse_args()
    network_attempts=[]
    if args.offline:
        def guard(event,arguments):
            if event in ('socket.connect','socket.getaddrinfo','socket.sendto'):
                network_attempts.append(event)
                raise RuntimeError('OUTBOUND_NETWORK_FORBIDDEN')
        sys.addaudithook(guard)
    import psutil
    from archive_system.ocr.tesseract import get_tesseract
    from archive_system.document_reading.reader import DocumentReader
    proc=psutil.Process(); peak=[proc.memory_info().rss]; stop=Event()
    def sample():
        while not stop.wait(0.01):
            peak[0]=max(peak[0],proc.memory_info().rss)
    monitor=Thread(target=sample,daemon=True);monitor.start()
    start=perf_counter(); engine=get_tesseract(args.model_dir,psm=args.psm);load_seconds=perf_counter()-start
    reader=DocumentReader(engine)
    manifest=json.loads((args.corpus/'manifest.json').read_text())
    ground_truth={r['id']:r for r in json.loads((ROOT/'evaluation/ground_truth.json').read_text())}
    assert manifest['ground_truth_sha256']==hashlib.sha256((ROOT/'evaluation/ground_truth.json').read_bytes()).hexdigest()
    rows=[]
    try:
        for item in manifest['records']:
            if item['split']!=args.split:continue
            path=args.corpus/item['file']
            assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
            result=reader.read(path,document_id=uuid4(),run_id=uuid4())
            assert len(result.pages)==1
            page=result.pages[0];reference=ground_truth[item['document_id']]['reference_text']
            rows.append(dict(document_id=item['document_id'],category=item['category'],variant=item['variant'],sha256=item['sha256'],source=page.source,
                             text=page.text,metrics=counts(reference,page.text),quality_reasons=result.quality_reasons,
                             timings_seconds=result.timings_seconds,regions=len(page.regions)))
    finally:
        stop.set();monitor.join();peak[0]=max(peak[0],proc.memory_info().rss)
    grouped={variant:aggregate([r['metrics'] for r in rows if r['variant']==variant]) for variant in sorted({r['variant'] for r in rows})}
    per_category={category:aggregate([r['metrics'] for r in rows if r['category']==category and r['variant']=='png']) for category in sorted({r['category'] for r in rows})}
    payload=dict(created_at=datetime.now(timezone.utc).isoformat(),engine=args.engine,engine_version=engine.version,split=args.split,
                 settings=dict(languages='ara+eng',psm=args.psm,oem='LSTM_ONLY',dpi=200,workers=1),
                 model_load_seconds=load_seconds,process_peak_rss_bytes=peak[0],memory_method='RSS sampled every 10ms, imports/model/pages included; short peaks may be missed',
                 platform=platform.platform(),python=platform.python_version(),logical_cpus=os.cpu_count(),
                 model_bytes=sum(p.stat().st_size for p in args.model_dir.glob('*.traineddata')),
                 documents=len({r['document_id'] for r in rows}),reading_cases=len(rows),network_guard=args.offline,network_attempts=network_attempts,
                 by_variant=grouped,png_by_category=per_category,rows=rows)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:payload[key] for key in ['documents','reading_cases','model_load_seconds','process_peak_rss_bytes','by_variant','network_attempts']},ensure_ascii=False))

if __name__=='__main__':main()
