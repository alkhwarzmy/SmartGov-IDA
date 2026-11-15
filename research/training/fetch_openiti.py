"""Explicit, bounded download of real printed Arabic line crops; NC research only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import random
import re
from urllib.request import urlopen

REVISION='946b966ddc35e82bd2a8ec39cc552d1c4f202b7e'
ROOT_URL=f'https://raw.githubusercontent.com/OpenITI/OCR_GS_Data/{REVISION}/'
BOOKS=[('book_IbnAthir.Kamil','train'),('book_IbnFaqihHamadhani.Buldan','train'),('book_IbnQutayba.Adab','validation'),('book_Jahiz.Hayawan','test')]

def fetch(url,limit=5*1024**2):
    with urlopen(url,timeout=45) as response:data=response.read(limit+1)
    if len(data)>limit:raise ValueError('DOWNLOAD_SIZE_LIMIT')
    return data

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--accept-noncommercial',action='store_true');parser.add_argument('--per-book',type=int,default=30);parser.add_argument('--test-book',choices=['book_Jahiz.Hayawan','book_Yacqubi.Tarikh'],default='book_Yacqubi.Tarikh');parser.add_argument('--reuse-existing',type=Path)
    args=parser.parse_args()
    if not args.accept_noncommercial:raise SystemExit('Explicit acceptance of CC BY-NC-SA 4.0 research-only use is required')
    if not 5<=args.per_book<=45:raise SystemExit('5–45 crops per book; conservative total limit <=180 images')
    output=args.output.resolve()
    if (output/'manifest.json').exists():raise SystemExit('Existing dataset manifest is preserved; use a new directory')
    output.mkdir(parents=True,exist_ok=True)
    readme=fetch(ROOT_URL+'README.md').decode()
    assert 'CC BY-NC-SA 4.0' in readme
    (output/'UPSTREAM-README.md').write_text(readme)
    selections=[]
    books=[(args.test_book if split=='test' else book,split) for book,split in BOOKS]
    reused={}
    if args.reuse_existing:
        previous=json.loads((args.reuse_existing/'manifest.json').read_text())
        if previous['revision']!=REVISION:raise ValueError('REUSE_REVISION_MISMATCH')
        reused={r['text_source']:r for r in previous['records']}
    for book,split in books:
        html=fetch(f'https://github.com/OpenITI/OCR_GS_Data/tree/{REVISION}/ara/{book}/7_final',limit=2*1024**2).decode()
        paths=sorted(set(re.findall(r'"path":"([^"\n]+)"',html)))
        paths=[p for p in paths if p.startswith(f'ara/{book}/7_final/') and p.endswith('.gt.txt')]
        if len(paths)<args.per_book:raise ValueError('INSUFFICIENT_LISTED_LINES')
        random.Random(20261115).shuffle(paths)
        for path in paths[:args.per_book]:selections.append((book,split,path))
    def download(item):
        book,split,text_path=item;image_path=text_path.removesuffix('.gt.txt')+'.png'
        old=reused.get(ROOT_URL+text_path)
        if old:
            image=(args.reuse_existing/old['image']).read_bytes();text=(args.reuse_existing/old['ground_truth']).read_bytes()
            if hashlib.sha256(image).hexdigest()!=old['image_sha256'] or hashlib.sha256(text).hexdigest()!=old['text_sha256']:raise ValueError('REUSE_CHECKSUM_MISMATCH')
        else:
            image=fetch(ROOT_URL+image_path);text=fetch(ROOT_URL+text_path,20000)
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(image)) as picture:
            if picture.width*picture.height>20_000_000:raise ValueError('PIXEL_LIMIT')
            dimensions=list(picture.size);picture.verify()
        reference=text.decode('utf-8').strip()
        if not reference or len(reference.splitlines())!=1:raise ValueError('NOT_A_SINGLE_REFERENCE_LINE')
        identity=book+'__'+Path(text_path).name.removesuffix('.gt.txt')
        folder=output/split;folder.mkdir(exist_ok=True)
        (folder/(identity+'.png')).write_bytes(image);(folder/(identity+'.gt.txt')).write_bytes(text)
        return dict(id=identity,split=split,source_group=book,source_revision=REVISION,license='CC-BY-NC-SA-4.0',
                    image=str((folder/(identity+'.png')).relative_to(output)),ground_truth=str((folder/(identity+'.gt.txt')).relative_to(output)),
                    image_sha256=hashlib.sha256(image).hexdigest(),text_sha256=hashlib.sha256(text).hexdigest(),
                    image_source=ROOT_URL+image_path,text_source=ROOT_URL+text_path,bytes=len(image)+len(text),dimensions=dimensions)
    records=list(ThreadPoolExecutor(max_workers=4).map(download,selections))
    assert sum(r['bytes'] for r in records)<=500*1024**2
    assert len(records)<=200
    for left in ('train','validation','test'):
        for right in ('train','validation','test'):
            if left==right:continue
            assert not {r['image_sha256'] for r in records if r['split']==left}&{r['image_sha256'] for r in records if r['split']==right}
            assert not {r['source_group'] for r in records if r['split']==left}&{r['source_group'] for r in records if r['split']==right}
    manifest=dict(created_at=datetime.now(timezone.utc).isoformat(),source='OpenITI/OCR_GS_Data',revision=REVISION,license='CC-BY-NC-SA-4.0',
                  permission='User explicitly accepted a separate noncommercial research experiment',
                  attribution='OpenITI OCR_GS_Data contributors; retain upstream attribution and license',
                  provenance='Real scanned printed book line crops with upstream human double-checked transcription; not generated text images',
                  limits='120 crops by default, conservatively counted as image units; original page IDs are unavailable, split by entire book to prevent page leakage',
                  records=records)
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    # Safe-to-share provenance contains source IDs/hashes only, never images or text.
    summary=dict(source=manifest['source'],revision=REVISION,license=manifest['license'],noncommercial_only=True,
                 counts={s:sum(r['split']==s for r in records) for s in ('train','validation','test')},bytes=sum(r['bytes'] for r in records),
                 source_groups={s:sorted({r['source_group'] for r in records if r['split']==s}) for s in ('train','validation','test')},
                 manifest_sha256=hashlib.sha256((output/'manifest.json').read_bytes()).hexdigest(),records=records)
    report=Path(__file__).resolve().parents[2]/'evaluation/research';report.mkdir(parents=True,exist_ok=True)
    (report/'openiti-provenance.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ('counts','bytes','source_groups')}))

if __name__=='__main__':main()
