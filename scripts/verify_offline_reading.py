"""Block every socket syscall (including native libraries) and trace networking."""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'evaluation/results'

def main():
    if not shutil.which('strace'):
        raise SystemExit('strace is required; no offline verification was performed')
    OUTPUT.mkdir(parents=True,exist_ok=True)
    trace=OUTPUT/'offline-network.trace'
    junit=OUTPUT/'offline-tests.xml'
    command=['strace','-f','-qq','-e','trace=network','-e','inject=socket:error=ENETUNREACH','-o',str(trace),sys.executable,'-m','pytest','-q',f'--junitxml={junit}']
    environment=os.environ.copy();environment['OMP_NUM_THREADS']='1'
    started=datetime.now(timezone.utc)
    process=subprocess.run(command,cwd=ROOT,env=environment)
    events=trace.read_text() if trace.exists() else None
    report=dict(started_at=started.isoformat(),finished_at=datetime.now(timezone.utc).isoformat(),command=command,
                exit_code=process.returncode,method='strace blocks all socket() syscalls with ENETUNREACH; trace=network includes native calls and child processes',
                trace_available=events is not None,network_trace_bytes=len(events.encode()) if events is not None else None,
                trace_sha256=hashlib.sha256(events.encode()).hexdigest() if events is not None else None,
                internet_socket_attempts=len(re.findall(r'AF_INET6?\b',events or '')),
                scope='Full local unit/integration/API/backup tests; ASGI in-process requests and local IPC only. Browser and clean offline installation have separate reports.')
    (OUTPUT/'offline-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    if process.returncode:
        return process.returncode
    if events is None or re.search(r'AF_INET6?\b',events):
        print('Internet socket attempt detected or trace unavailable; offline no-attempt claim not established',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
