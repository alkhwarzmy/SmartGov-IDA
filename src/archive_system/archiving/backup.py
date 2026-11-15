"""Consistent snapshots under the service lock; restore only into a new directory."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from uuid import uuid4
import zipfile
from archive_system.archiving.files import verified_original
from archive_system.domain.models import Document

class BackupError(ValueError):pass


def backup(service,destination: Path):
    destination=destination.resolve()
    if destination.exists():raise BackupError('BACKUP_DESTINATION_EXISTS')
    destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temporary=destination.with_name(destination.name+'.'+str(uuid4())+'.tmp')
    manifest={'version':1,'created_at':datetime.now(timezone.utc).isoformat(),'files':{}}
    try:
        with service.lock,tempfile.TemporaryDirectory(prefix='smartgov-backup-') as folder:
            database=Path(folder)/'archive.sqlite3'
            with service.store.connect() as source:
                target=sqlite3.connect(database)
                try:source.backup(target)
                finally:target.close()
                documents=[Document.model_validate_json(r['payload']) for r in source.execute('SELECT payload FROM documents')]
            with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED) as archive:
                temporary.chmod(0o600)
                def add(path,name,expected=None):
                    digest=hashlib.sha256();size=0
                    with path.open('rb') as stream,archive.open(name,'w') as output:
                        for chunk in iter(lambda:stream.read(64*1024),b''):
                            digest.update(chunk);size+=len(chunk);output.write(chunk)
                    if expected and digest.hexdigest()!=expected:raise BackupError('ORIGINAL_CHANGED_DURING_BACKUP')
                    manifest['files'][name]={'sha256':digest.hexdigest(),'bytes':size}
                add(database,'archive.sqlite3')
                for document in documents:
                    add(verified_original(service.root,document.original_path,document.sha256),document.original_path,document.sha256)
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            with temporary.open('rb') as stream:os.fsync(stream.fileno())
            # Never replace a user backup that appeared while making this one.
            os.link(temporary,destination)
            temporary.unlink()
        return destination
    finally:temporary.unlink(missing_ok=True)


def restore(archive_path: Path,destination: Path):
    destination=destination.resolve()
    if destination.exists():raise BackupError('RESTORE_DESTINATION_MUST_NOT_EXIST')
    destination.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    staging=destination.with_name('.'+destination.name+'.restore-'+str(uuid4()))
    staging.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries=archive.infolist();names=[e.filename for e in entries]
            if len(names)!=len(set(names)) or len(names)>100000 or sum(e.file_size for e in entries)>10*1024**3:
                raise BackupError('UNSAFE_BACKUP_SIZE_OR_DUPLICATES')
            manifest_entry=next((e for e in entries if e.filename=='manifest.json'),None)
            if manifest_entry is None or manifest_entry.file_size>2*1024**2:raise BackupError('INVALID_MANIFEST')
            manifest=json.loads(archive.read('manifest.json'))
            if manifest.get('version')!=1 or not isinstance(manifest.get('files'),dict):raise BackupError('INVALID_MANIFEST')
            expected=manifest['files']
            if 'archive.sqlite3' not in expected or set(names)!=(set(expected)|{'manifest.json'}):raise BackupError('INVALID_BACKUP_CONTENTS')
            for entry in entries:
                if entry.filename=='manifest.json':continue
                name=entry.filename
                if not (name=='archive.sqlite3' or re.fullmatch(r'originals/[0-9a-f-]{36}\.(pdf|png|jpg)',name)):
                    raise BackupError('UNSAFE_BACKUP_PATH')
                if (entry.external_attr>>16)&0o170000==0o120000:raise BackupError('SYMLINK_BACKUP_ENTRY')
                output=staging/name;output.parent.mkdir(mode=0o700,exist_ok=True)
                digest=hashlib.sha256();size=0
                with archive.open(entry) as source,output.open('xb') as target:
                    output.chmod(0o600)
                    for chunk in iter(lambda:source.read(64*1024),b''):
                        size+=len(chunk);digest.update(chunk);target.write(chunk)
                    target.flush();os.fsync(target.fileno())
                if size!=expected[name]['bytes'] or digest.hexdigest()!=expected[name]['sha256']:raise BackupError('BACKUP_CHECKSUM_MISMATCH')
        connection=sqlite3.connect((staging/'archive.sqlite3').as_uri()+'?mode=ro',uri=True)
        try:
            if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or connection.execute('PRAGMA foreign_key_check').fetchone():raise BackupError('INVALID_DATABASE')
            if connection.execute('PRAGMA user_version').fetchone()[0]!=1:raise BackupError('UNSUPPORTED_DATABASE_VERSION')
            documents=[Document.model_validate_json(row[0]) for row in connection.execute('SELECT payload FROM documents')]
            if set(expected)!={'archive.sqlite3',*(d.original_path for d in documents)}:raise BackupError('ORIGINAL_MANIFEST_MISMATCH')
            for document in documents:verified_original(staging,document.original_path,document.sha256)
            connection.execute('SELECT count(*) FROM document_fts').fetchone()
        finally:connection.close()
        if destination.exists():raise BackupError('RESTORE_DESTINATION_MUST_NOT_EXIST')
        staging.rename(destination)
        return destination
    except (zipfile.BadZipFile,KeyError,TypeError,json.JSONDecodeError,sqlite3.DatabaseError) as error:
        raise BackupError('INVALID_BACKUP') from error
    finally:
        if staging.exists():shutil.rmtree(staging)
