"""Fine-tune floating-point Tesseract Arabic on licensed real line images, offline."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from threading import Event,Thread
from time import perf_counter
import unicodedata

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT)]
from evaluation.metrics import counts,aggregate

def training_transcript(raw):
    # Preserve upstream GT; only the training copy maps an unrepresentable comma.
    return unicodedata.normalize('NFC',raw.strip()).replace('،',',')

def rtl_wordstr_box(text,width,height):
    from bidi.algorithm import get_display
    return f'WordStr 0 0 {width} {height} 0 #{get_display(text)}\n\t 0 0 {width} {height} 0\n'
BASE_SHA256='ab9d157d8e38ca00e7e39c7d5363a5239e053f5b0dbdb3167dde9d8124335896'

def validate_dataset(manifest):
    records=manifest['records']
    if manifest.get('license')!='CC-BY-NC-SA-4.0':raise ValueError('UNREVIEWED_DATASET_LICENSE')
    for a,b in [('train','validation'),('train','test'),('validation','test')]:
        for key in ['source_group','image_sha256','text_sha256']:
            if {r[key] for r in records if r['split']==a}&{r[key] for r in records if r['split']==b}:raise ValueError('DATA_SPLIT_LEAKAGE')
    if not all(any(r['split']==s for r in records) for s in ('train','validation','test')):raise ValueError('EMPTY_SPLIT')
    return records


def evaluate(model_dir,records,dataset):
    import tesserocr
    from PIL import Image
    start=perf_counter();api=tesserocr.PyTessBaseAPI(path=str(model_dir),lang='ara',oem=tesserocr.OEM.LSTM_ONLY,psm=tesserocr.PSM.RAW_LINE)
    load=perf_counter()-start
    measurements={s:[] for s in ('validation','test')};elapsed={s:0.0 for s in measurements}
    try:
        for item in records:
            split=item['split']
            if split not in measurements:continue
            reference=(dataset/item['ground_truth']).read_text().strip()+'\n'
            with Image.open(dataset/item['image']) as image:
                start=perf_counter();api.SetImage(image.convert('RGB'));hypothesis=api.GetUTF8Text();elapsed[split]+=perf_counter()-start
            measurements[split].append(counts(reference,hypothesis));api.Clear()
    finally:api.End()
    return dict(model_load_seconds=load,seconds=elapsed,**{s:aggregate(m) for s,m in measurements.items()})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',type=Path,required=True);parser.add_argument('--base-model',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--iterations',type=int,default=300);parser.add_argument('--accept-noncommercial',action='store_true')
    args=parser.parse_args();output=args.output.resolve();dataset=args.dataset.resolve()
    if not args.accept_noncommercial:raise SystemExit('Research-only noncommercial permission is required')
    if output.is_relative_to(ROOT) or dataset.is_relative_to(ROOT):raise SystemExit('Datasets and weights must remain outside Git')
    if output.exists():raise SystemExit('Preserve existing runs; select a new output directory')
    if not 10<=args.iterations<=1000:raise SystemExit('Bounded pilot: 10–1000 iterations')
    if hashlib.sha256(args.base_model.read_bytes()).hexdigest()!=BASE_SHA256:raise SystemExit('BASE_MODEL_CHECKSUM_MISMATCH')
    manifest=json.loads((dataset/'manifest.json').read_text());records=validate_dataset(manifest)
    for item in records:
        for field,checksum in [('image','image_sha256'),('ground_truth','text_sha256')]:
            if hashlib.sha256((dataset/item[field]).read_bytes()).hexdigest()!=item[checksum]:raise ValueError('DATA_CHECKSUM_MISMATCH')
    output.mkdir(parents=True);base=output/'base';base.mkdir();shutil.copy2(args.base_model,base/'ara.traineddata')
    environment=os.environ.copy();environment['OMP_NUM_THREADS']='2'
    log=output/'training.log'
    commands=[]
    def run(command):
        commands.append(command)
        with log.open('a') as stream:subprocess.run(command,env=environment,stdout=stream,stderr=stream,check=True)
    run(['combine_tessdata','-e',str(base/'ara.traineddata'),str(base/'ara.lstm')])
    from PIL import Image
    lstmf={s:[] for s in ('train','validation')}
    for item in records:
        if item['split'] not in lstmf:continue  # test images never enter training format generation
        folder=output/'lines'/item['split'];folder.mkdir(parents=True,exist_ok=True);stem=folder/item['id'].replace('.','_')
        shutil.copy2(dataset/item['image'],stem.with_suffix('.png'))
        text=training_transcript((dataset/item['ground_truth']).read_text())
        with Image.open(stem.with_suffix('.png')) as image:width,height=image.size
        stem.with_suffix('.gt.txt').write_text(text+'\n')
        stem.with_suffix('.box').write_text(rtl_wordstr_box(text,width,height))
        run(['tesseract',str(stem.with_suffix('.png')),str(stem),'--tessdata-dir',str(base),'-l','ara','--psm','13','/usr/share/tesseract-ocr/5/tessdata/configs/lstm.train'])
        if not stem.with_suffix('.lstmf').is_file():raise RuntimeError('LSTMF_GENERATION_FAILED')
        lstmf[item['split']].append(str(stem.with_suffix('.lstmf')))
    for split,files in lstmf.items():(output/(split+'.list')).write_text('\n'.join(files)+'\n')
    run(['lstmeval','--model',str(base/'ara.traineddata'),'--eval_listfile',str(output/'train.list'),'--verbosity','0'])
    if "Can't encode transcription" in log.read_text():raise RuntimeError('UNENCODABLE_TRAINING_TRANSCRIPTION')
    import psutil
    peak=[psutil.Process().memory_info().rss];stop=Event()
    def sample():
        process=psutil.Process()
        while not stop.wait(.02):
            total=process.memory_info().rss
            for child in process.children(recursive=True):
                try:total+=child.memory_info().rss
                except psutil.Error:pass
            peak[0]=max(peak[0],total)
    monitor=Thread(target=sample,daemon=True);monitor.start();start=perf_counter()
    try:
        run(['lstmtraining','--continue_from',str(base/'ara.lstm'),'--traineddata',str(base/'ara.traineddata'),'--model_output',str(output/'arabic'),
             '--train_listfile',str(output/'train.list'),'--eval_listfile',str(output/'validation.list'),'--max_iterations',str(args.iterations),
             '--learning_rate','0.0001','--reset_learning_rate','true','--target_error_rate','0','--max_image_MB','256','--sequential_training','true','--debug_interval','0'])
        train_seconds=perf_counter()-start
        checkpoints=list(output.glob('arabic_*_*.checkpoint'))
        # The native trainer emits named best-training-error checkpoints. Validation
        # is used below to compare the trained candidate with the unchanged base.
        checkpoint=min(checkpoints,key=lambda p:float(p.name.split('_')[1])) if checkpoints else output/'arabic_checkpoint'
        if not checkpoint.is_file():raise RuntimeError('TRAINING_CHECKPOINT_MISSING')
        tuned=output/'tuned';tuned.mkdir()
        run(['lstmtraining','--stop_training','--continue_from',str(checkpoint),'--traineddata',str(base/'ara.traineddata'),'--model_output',str(tuned/'ara.traineddata')])
        baseline=evaluate(base,records,dataset);candidate=evaluate(tuned,records,dataset)
    finally:stop.set();monitor.join()
    before=baseline['validation']['normalized']['cer'];after=candidate['validation']['normalized']['cer']
    report=dict(created_at=datetime.now(timezone.utc).isoformat(),experiment='real-printed-Arabic-OCR-finetuning',noncommercial_only=True,default_application_changed=False,
                dataset_manifest_sha256=hashlib.sha256((dataset/'manifest.json').read_bytes()).hexdigest(),dataset_revision=manifest['revision'],
                split_counts={s:sum(r['split']==s for r in records) for s in ('train','validation','test')},split_policy='Entire books disjoint; no test images converted to lstmf or used for selection',
                baseline_model_sha256=BASE_SHA256,baseline_source_revision='e12c65a915945e4c28e237a9b52bc4a8f39a0cec',trained_model_sha256=hashlib.sha256((tuned/'ara.traineddata').read_bytes()).hexdigest(),
                parameters=dict(max_iterations=args.iterations,learning_rate=.0001,psm=13,language='ara',training_unicode='NFC; Arabic comma mapped to ASCII comma for base-charset coverage; original GT preserved',rtl='visual-order WordStr boxes',reset_learning_rate=True,threads=2,sequential_training=True),
                training_seconds=train_seconds,peak_rss_including_children_bytes=peak[0],baseline=baseline,candidate=candidate,
                validation_improved=after<before,eligible_for_noncommercial_trial=after<before,
                trained_model_path=str(tuned/'ara.traineddata'),training_log_path=str(log),commands=commands,
                limitations=['Only 60 training line crops and four final books; not a government document or invoice benchmark','A previous setup-debugging book was excluded and replaced with a new untouched test book before this run','Model remains outside the default application regardless of test result','Raw metrics retain upstream Unicode; normalized metrics follow documented evaluation policy','Training on a small real sample does not establish general accuracy or eliminate review'])
    report_path=ROOT/'evaluation/research/ocr-finetuning.json';report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:report[key] for key in ('training_seconds','peak_rss_including_children_bytes','split_counts','validation_improved','trained_model_sha256')}))
    for split in ('validation','test'):print(split,'base',baseline[split]['normalized'],'tuned',candidate[split]['normalized'])

if __name__=='__main__':main()
