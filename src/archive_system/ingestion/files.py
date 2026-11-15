"""Streaming, bounded local ingestion; original filenames never become paths."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import BinaryIO
from uuid import uuid4

class InputError(ValueError):pass

@dataclass(frozen=True)
class ReceivedFile:
    temporary: Path
    sha256: str
    size: int
    media_type: str
    extension: str
    original_name: str


def receive(stream: BinaryIO, filename: str, root: Path, max_bytes: int) -> ReceivedFile:
    incoming=root/'incoming';incoming.mkdir(mode=0o700,exist_ok=True)
    temporary=incoming/str(uuid4());digest=hashlib.sha256();size=0;header=b''
    try:
        with temporary.open('xb') as output:
            temporary.chmod(0o600)
            while chunk:=stream.read(64*1024):
                size+=len(chunk)
                if size>max_bytes:raise InputError('FILE_TOO_LARGE')
                if len(header)<8:header=(header+chunk)[:8]
                digest.update(chunk);output.write(chunk)
            output.flush()
            import os
            os.fsync(output.fileno())
        if not size:raise InputError('EMPTY_FILE')
        if header.startswith(b'%PDF-'):media,extension='application/pdf','.pdf'
        elif header.startswith(b'\x89PNG\r\n\x1a\n'):media,extension='image/png','.png'
        elif header.startswith(b'\xff\xd8\xff'):media,extension='image/jpeg','.jpg'
        else:raise InputError('UNSUPPORTED_FILE_TYPE')
        name=re.sub(r'[\x00-\x1f\x7f]', '',filename.replace('\\','/').split('/')[-1]).strip()[:200] or 'document'+extension
        return ReceivedFile(temporary,digest.hexdigest(),size,media,extension,name)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
