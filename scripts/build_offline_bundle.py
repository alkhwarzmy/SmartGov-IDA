"""Online preparation of a hash-locked Linux x86_64 / Python 3.12 bundle."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--models',type=Path,default=Path('/workspace/smartgov-models/tesseract'))
    args=parser.parse_args();out=args.output.resolve()
    if out.exists():raise SystemExit('Choose a new bundle directory; existing files will not be overwritten')
    out.mkdir(parents=True);wheels=out/'wheelhouse';wheels.mkdir()
    subprocess.run([sys.executable,'-m','pip','download','--no-cache-dir','--only-binary=:all:','--dest',str(wheels),'-r',str(ROOT/'requirements-runtime.lock')],check=True)
    subprocess.run([sys.executable,'-m','pip','wheel','--no-cache-dir','--no-deps','--wheel-dir',str(wheels),str(ROOT)],check=True)
    app_wheel=next(wheels.glob('smartgov_archive-*.whl'))
    with ZipFile(app_wheel) as wheel:
        assert 'archive_system/ui/static/index.html' in wheel.namelist()
        assert 'archive_system/application/service.py' in wheel.namelist()
    locked=[]
    for path in sorted(wheels.glob('*.whl')):
        with ZipFile(path) as wheel:
            info=next(name for name in wheel.namelist() if name.endswith('.dist-info/METADATA'))
            from email.parser import Parser
            fields=Parser().parsestr(wheel.read(info).decode())
        locked.append(f"{fields['Name']}=={fields['Version']} --hash=sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}")
    (out/'requirements-offline.lock').write_text('\n'.join(locked)+'\n')
    models=out/'models/tesseract';models.mkdir(parents=True)
    sys.path.insert(0,str(ROOT/'src'))
    from archive_system.ocr.tesseract import WEIGHTS
    for name,digest in WEIGHTS.items():
        source=args.models/name
        assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
        shutil.copy2(source,models/name)
    shutil.copytree(ROOT/'docs',out/'docs')
    notices=out/'licenses';notices.mkdir()
    for line in (ROOT/'requirements-runtime.lock').read_text().splitlines():
        name=line.split('==')[0];dist=metadata.distribution(name)
        for relative in dist.files or []:
            text=str(relative).lower()
            if '.dist-info/' in text and any(key in text for key in ('license','notice','copyright')):
                source=Path(dist.locate_file(relative))
                if source.is_file():
                    target=notices/name/str(relative);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    for name in ['ocr-assets.json','dependency-licenses.json']:
        shutil.copy2(ROOT/'evaluation'/name,out/name)
    examples=out/'examples';examples.mkdir()
    shutil.copy2('/workspace/smartgov-corpus/v1/development-letter-01-digital.pdf',examples/'letter.pdf')
    shutil.copy2('/workspace/smartgov-corpus/v1/development-invoice-01.png',examples/'invoice.png')
    shutil.copy2(ROOT/'evaluation/data/development-invoice-01.txt',examples/'invoice.txt')
    (examples/'bad.pdf').write_bytes(b'%PDF-invalid-synthetic-example')
    (out/'verify_bundle.py').write_text('''import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent
manifest=json.loads((root/'bundle-manifest.json').read_text())
for name,digest in manifest['files'].items():
    path=root/name
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise SystemExit('Bundle integrity check failed: '+name)
print('Bundle SHA-256 checks passed.')
''')
    (out/'install.sh').write_text('''#!/bin/sh
set -eu
BUNDLE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_VENV=${1:-"$BUNDLE_DIR/venv"}
if [ -e "$INSTALL_VENV" ]; then echo "Choose a new virtual environment directory." >&2; exit 1; fi
python3 -c 'import sys,platform; assert sys.version_info[:2]==(3,12) and sys.platform=="linux" and platform.machine()=="x86_64", "Requires Linux x86_64 and Python 3.12"'
python3 "$BUNDLE_DIR/verify_bundle.py"
python3 -m venv "$INSTALL_VENV"
"$INSTALL_VENV/bin/python" -m pip --disable-pip-version-check install --no-index --find-links "$BUNDLE_DIR/wheelhouse" --require-hashes -r "$BUNDLE_DIR/requirements-offline.lock"
echo "Installed. Start with:"
printf '%s\\n' "$INSTALL_VENV/bin/python -m archive_system.cli --data-dir /path/to/local-data --model-dir $BUNDLE_DIR/models/tesseract serve"
''')
    (out/'install.sh').chmod(0o755)
    (out/'README-OFFLINE.md').write_text('''# SmartGov-IDA local MVP 0.2.0

Target: Linux x86_64, Python 3.12 with venv/ensurepip and glibc >= 2.28. Tested on this cloud Linux machine only; Windows/macOS packages are not included. Prepare Python/system prerequisites before disconnecting.

Run `sh install.sh /absolute/path/to/new-venv` from the extracted bundle. Installation uses only bundled wheels and verifies their SHA-256 hashes. Models are local, pinned ara+eng assets. No Paddle inference is included.

Start with `/absolute/path/to/new-venv/bin/python -m archive_system.cli --data-dir /absolute/path/to/private-data --model-dir /absolute/path/to/bundle/models/tesseract serve`. The service binds only to the local loopback interface on port 8765. Keep one process. Visit that local address in your own browser; no external deployment is configured.

See docs/user-guide.md and docs/technical-guide.md. OCR is assistive and always requires full-page and field review. Only the two documented printed document types are supported. Calendar is never inferred; complex tables, handwriting, government production use, and semantic search are outside scope.

Preserve all docs/licenses and licenses/ notices when redistributing. Project code licensing remains the repository owner's decision. Examples are fictional CC0 content with separate embedded font notices. Backups contain original documents and extracted text in plaintext; protect their directory and device.
''')
    manifest=dict(version='0.2.0',target='linux-x86_64-cpython312',files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()})
    (out/'bundle-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'bundle':str(out),'wheels':len(locked),'bytes':sum(p.stat().st_size for p in out.rglob('*') if p.is_file())}))

if __name__=='__main__':main()
