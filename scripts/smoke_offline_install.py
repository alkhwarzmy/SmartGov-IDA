"""Exercise only the installed wheel in a fresh, socket-blocked offline install."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--work',type=Path,required=True)
    args=parser.parse_args();bundle=args.bundle.resolve();work=args.work.resolve()
    if work.exists():raise SystemExit('Choose a fresh test directory')
    work.mkdir(parents=True);venv=work/'venv';python=venv/'bin/python'
    environment=os.environ.copy();environment.pop('PYTHONPATH',None);environment['PYTHONNOUSERSITE']='1';environment['OMP_NUM_THREADS']='1'
    install_trace=work/'install-network.trace'
    subprocess.run(['strace','-f','-qq','-e','trace=network','-e','inject=socket:error=ENETUNREACH','-o',str(install_trace),'sh',str(bundle/'install.sh'),str(venv)],cwd=work,env=environment,check=True)
    install_events=install_trace.read_text()
    # pip probes IPv6 capability at import, even with --no-index. Socket creation
    # is not an outbound connection; both probes are blocked by strace here.
    assert not re.search(r'\b(?:connect|sendto|sendmsg)\(',install_events)
    blocked_socket_creations=len(re.findall(r'socket\(AF_INET6?[^\n]*INJECTED',install_events))
    imported=subprocess.check_output([str(python),'-c','import archive_system;print(archive_system.__file__)'],cwd=work,env=environment,text=True).strip()
    assert str(venv) in imported,imported
    # The guard is test instrumentation passed to the clean interpreter, not a runtime dependency.
    guard='''import sys,runpy,json
from pathlib import Path
attempts=Path(sys.argv.pop(1))
def guard(event,args):
    blocked=False
    if event=='socket.connect' and isinstance(args[1],tuple):blocked=args[1][0] not in ('127.0.0.1','::1','localhost')
    if event=='socket.getaddrinfo':blocked=args[0] not in ('127.0.0.1','::1','localhost',None)
    if blocked:
        with attempts.open('a') as output:output.write(event+'\\n')
        raise RuntimeError('EXTERNAL_NETWORK_FORBIDDEN')
sys.addaudithook(guard)
sys.argv=['archive']+sys.argv[1:]
runpy.run_module('archive_system.cli',run_name='__main__')
'''
    attempts=work/'external-attempts.log';attempts.write_text('')
    @contextmanager
    def server(data,tag):
        log=(work/(tag+'.log')).open('w')
        command=['strace','-f','-qq','-e','trace=network','-o',str(work/(tag+'-network.trace')),str(python),'-c',guard,str(attempts),'--data-dir',str(data),'--model-dir',str(bundle/'models/tesseract'),'serve','--port','8766']
        process=subprocess.Popen(command,cwd=work,env=environment,stdout=log,stderr=log,start_new_session=True)
        try:
            deadline=time.monotonic()+25
            while True:
                if process.poll() is not None:raise RuntimeError('Clean server exited; inspect local test log')
                try:
                    if request('GET','/api/health')[0]==200:break
                except (OSError,http.client.HTTPException):pass
                if time.monotonic()>deadline:raise RuntimeError('Clean server readiness timed out')
                time.sleep(.1)
            yield
        finally:
            # Signal the isolated group created by this test (strace and its own server).
            import signal
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            log.close()
            import fcntl
            deadline=time.monotonic()+5
            with (data/'.process.lock').open('a') as lock:
                while True:
                    try:
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_UN);break
                    except BlockingIOError:
                        if time.monotonic()>deadline:raise RuntimeError('Server did not release its archive lock')
                        time.sleep(.05)
    def request(method,path,body=None,content_type='application/json'):
        connection=http.client.HTTPConnection('127.0.0.1',8766,timeout=60)
        try:
            payload=json.dumps(body).encode() if isinstance(body,dict) else body
            connection.request(method,path,body=payload,headers={'X-Archive-Request':'local','Content-Type':content_type})
            response=connection.getresponse();data=response.read()
            return response.status,data
        finally:connection.close()
    def json_request(method,path,body=None):
        status,data=request(method,path,body)
        assert status in (200,201),(status,data[:300])
        return json.loads(data)
    def upload(path):
        boundary='smartgov-'+uuid4().hex
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()+path.read_bytes()+f'\r\n--{boundary}--\r\n'.encode())
        status,data=request('POST','/api/documents',body,f'multipart/form-data; boundary={boundary}')
        assert status==201,(status,data[:300])
        return json.loads(data)['document']
    data_dir=work/'data'
    with server(data_dir,'clean-server'):
        assert b'lang="ar"' in request('GET','/')[1]
        document=upload(bundle/'examples/letter.pdf');identity=document['id'];run=document['current_run_id']
        field=next(f for f in document['fields'] if f['name']=='date')
        document=json_request('POST',f'/api/documents/{identity}/reviews',dict(target='field',value='2026-11-15',calendar='gregorian',field_id=field['id'],expected_run_id=run))
        field=next(f for f in document['fields'] if f['name']=='subject')
        json_request('POST',f'/api/documents/{identity}/reviews',dict(target='field',value='offline verified archive',field_id=field['id'],expected_run_id=run))
        document=json_request('POST',f'/api/documents/{identity}/confirm',dict(expected_run_id=run,acknowledged=True))
        assert document['status']=='completed'
        assert json_request('GET','/api/documents?q=offline%20verified')['items'][0]['id']==identity
        assert hashlib.sha256(request('GET',f'/api/documents/{identity}/original')[1]).digest()==hashlib.sha256((bundle/'examples/letter.pdf').read_bytes()).digest()
        assert request('GET',f'/api/documents/{identity}/preview')[1].startswith(b'\x89PNG')
        image=upload(bundle/'examples/invoice.png')
        image_id=image['id'];image_run=image['current_run_id']
        assert image['pages'][0]['source']=='ocr'
        json_request('POST',f'/api/documents/{image_id}/reviews',dict(target='page',page_number=1,value=(bundle/'examples/invoice.txt').read_text(),expected_run_id=image_run))
        values=dict(invoice_number='INV-101',date='2026-11-15',supplier='متجر الورق الوهمي',total='125.50',currency='SAR')
        for field in image['fields']:
            json_request('POST',f'/api/documents/{image_id}/reviews',dict(target='field',value=values[field['name']],field_id=field['id'],calendar='gregorian' if field['name']=='date' else 'unknown',expected_run_id=image_run))
        assert json_request('POST',f'/api/documents/{image_id}/confirm',dict(expected_run_id=image_run,acknowledged=True))['status']=='completed'
        failed=upload(bundle/'examples/bad.pdf');assert failed['status']=='failed'
        assert request('GET',f'/api/documents/{failed["id"]}/original')[1]==(bundle/'examples/bad.pdf').read_bytes()
    backup_file=work/'snapshot.zip'
    subprocess.run([str(python),'-m','archive_system.cli','--data-dir',str(data_dir),'--model-dir',str(bundle/'models/tesseract'),'backup',str(backup_file)],cwd=work,env=environment,check=True)
    restored=work/'restored'
    subprocess.run([str(python),'-m','archive_system.cli','restore',str(backup_file),str(restored)],cwd=work,env=environment,check=True)
    with server(restored,'restored-server'):
        again=json_request('GET',f'/api/documents/{identity}')
        assert again==document
        assert json_request('GET','/api/documents?q=offline%20verified')['items'][0]['id']==identity
        assert json_request('GET',f'/api/documents/{image_id}')['status']=='completed'
    assert attempts.read_text()==''
    native_external=[]
    for trace in work.glob('*server-network.trace'):
        text=trace.read_text()
        native_external += [ip for ip in re.findall(r'inet_addr\("([^"]+)"\)',text) if ip!='127.0.0.1']
        native_external += [ip for ip in re.findall(r'inet_pton\(AF_INET6, "([^"]+)"',text) if ip!='::1']
    assert not native_external,native_external
    report=dict(created_at=datetime.now(timezone.utc).isoformat(),clean_venv=str(venv),installed_module=imported,bundle=str(bundle),
                install_network_blocked=True,install_blocked_ip_socket_creations=blocked_socket_creations,install_outbound_connection_calls=0,external_python_attempts=0,native_external_addresses=native_external,
                local_http_cycle=True,digital_review_search_download=True,image_ocr_transcription_and_field_review=True,failed_original_retained=True,
                local_preview=True,consistent_backup_and_restore=True,restored_search_and_reviews_equal=True,
                limitations='Linux x86_64 / Python 3.12 only; Python and OS prerequisites preinstalled. HTTP loopback is permitted; external Python connections blocked, native trace checked. Separate API tests block all socket creation.')
    (ROOT/'evaluation/results/offline-clean-install.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))

if __name__=='__main__':main()
