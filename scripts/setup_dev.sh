#!/bin/sh
set -eu
cd /workspace/SmartGov-IDA
DEV_PYTHON=/workspace/smartgov-venv/bin/python
if [ ! -x "$DEV_PYTHON" ]; then python3 -m venv /workspace/smartgov-venv; fi
"$DEV_PYTHON" -m pip install --no-cache-dir -r requirements-dev.lock
"$DEV_PYTHON" -m pip install --no-cache-dir --no-deps -e .
"$DEV_PYTHON" scripts/fetch_tesseract_models.py --output /workspace/smartgov-models/tesseract
if [ ! -e /workspace/smartgov-corpus/v1/manifest.json ]; then
  "$DEV_PYTHON" scripts/generate_reading_corpus.py --output /workspace/smartgov-corpus/v1
fi
OMP_NUM_THREADS=1 "$DEV_PYTHON" -m pytest -q
