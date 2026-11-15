"""Explicit online setup only. Runtime OCR never calls this script."""
import argparse
import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from archive_system.ocr.tesseract import WEIGHTS
REVISION='87416418657359cb625c412a48b6e1d6d41c29bd'

def fetch(destination):
    destination.mkdir(parents=True,exist_ok=True)
    for name,digest in WEIGHTS.items():
        path=destination/name
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise RuntimeError(f'Existing model changed: {name}; inspect before replacing')
            continue
        with urlopen(f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{REVISION}/{name}',timeout=60) as response:
            data=response.read(10*1024**2+1)
        if len(data)>10*1024**2 or hashlib.sha256(data).hexdigest()!=digest:
            raise RuntimeError(f'Untrusted model bytes: {name}')
        temporary=path.with_suffix('.tmp');temporary.write_bytes(data);temporary.replace(path)
    print('Two pinned local language assets verified.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    fetch(parser.parse_args().output)
