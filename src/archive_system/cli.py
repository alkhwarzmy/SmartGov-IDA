import argparse
import json
import os
from pathlib import Path
from archive_system.application.service import ArchiveService
from archive_system.archiving.backup import backup, restore
from archive_system.config import Settings


def main():
    parser=argparse.ArgumentParser(description='SmartGov local archive — never sends document contents to external services')
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--model-dir',type=Path)
    commands=parser.add_subparsers(dest='command',required=True)
    serve=commands.add_parser('serve');serve.add_argument('--port',type=int,default=8765)
    process=commands.add_parser('process');process.add_argument('file',type=Path)
    search=commands.add_parser('search');search.add_argument('query',nargs='?',default='')
    back=commands.add_parser('backup');back.add_argument('destination',type=Path)
    recovery=commands.add_parser('restore');recovery.add_argument('archive',type=Path);recovery.add_argument('destination',type=Path)
    arguments=parser.parse_args()
    if arguments.data_dir:os.environ['SMARTGOV_DATA_DIR']=str(arguments.data_dir)
    if arguments.model_dir:os.environ['SMARTGOV_MODEL_DIR']=str(arguments.model_dir)
    os.environ.setdefault('OMP_NUM_THREADS','1')
    os.umask(0o077)
    if arguments.command=='restore':
        print(json.dumps({'restored_to':str(restore(arguments.archive,arguments.destination))}));return
    if arguments.command=='serve':
        if not 1024<=arguments.port<=65535:parser.error('port must be between 1024 and 65535')
        import uvicorn
        uvicorn.run('archive_system.bootstrap:app_factory',factory=True,host='127.0.0.1',port=arguments.port,workers=1,access_log=False)
        return
    service=ArchiveService(Settings.from_environment())
    try:
        if arguments.command=='process':
            with arguments.file.open('rb') as stream:result=service.ingest(stream,arguments.file.name)
            print(json.dumps({key:result['document'][key] for key in ('id','status','sha256')},ensure_ascii=False))
        elif arguments.command=='search':
            print(json.dumps([{'id':d['id'],'name':d['original_name'],'status':d['status']} for d in service.search(arguments.query)],ensure_ascii=False))
        else:print(json.dumps({'backup':str(backup(service,arguments.destination))}))
    finally:service.close()

if __name__=='__main__':main()
