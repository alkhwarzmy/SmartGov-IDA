import json
from pathlib import Path
import zipfile
import pytest
from archive_system.archiving.backup import backup, restore, BackupError
from archive_system.application.service import ArchiveService
from archive_system.config import Settings
from test_storage import service,upload

def test_consistent_backup_restore_preserves_reviews_text_and_original(service,tmp_path):
    doc=upload(service)['document']
    subject=next(f for f in doc['fields'] if f['name']=='subject')
    doc=service.review(doc['id'],'field','كلمة استعادة فريدة',field_id=subject['id'])
    archive=backup(service,tmp_path/'backup.zip')
    target=restore(archive,tmp_path/'restored')
    restored=ArchiveService(Settings(runtime_dir=target,model_dir=service.settings.model_dir))
    try:
        result=restored.get(doc['id'])
        assert result==doc
        assert restored.search('استعادة')[0]['id']==doc['id']
        assert restored.original(doc['id'])[0].read_bytes()==service.original(doc['id'])[0].read_bytes()
    finally:restored.close()
    with pytest.raises(BackupError):backup(service,archive)
    with pytest.raises(BackupError):restore(archive,target)

def test_restore_rejects_traversal_and_changed_bytes_without_touching_destination(service,tmp_path):
    upload(service);archive=backup(service,tmp_path/'good.zip')
    for mode in ('changed','traversal'):
        bad=tmp_path/f'{mode}.zip'
        with zipfile.ZipFile(archive) as source,zipfile.ZipFile(bad,'w') as output:
            for name in source.namelist():
                data=source.read(name)
                if mode=='changed' and name.endswith('.pdf'):data=b'changed'
                output.writestr(name,data)
            if mode=='traversal':output.writestr('../outside.txt','unsafe')
        destination=tmp_path/mode
        with pytest.raises(BackupError):restore(bad,destination)
        assert not destination.exists()
        assert not (tmp_path/'outside.txt').exists()
