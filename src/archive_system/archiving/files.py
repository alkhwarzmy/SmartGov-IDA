import hashlib
from pathlib import Path

class IntegrityError(RuntimeError):pass

def verified_original(root: Path, relative: str, digest: str) -> Path:
    path=root/relative
    if path.is_symlink() or path.resolve().parent!=(root/'originals').resolve() or not path.is_file():
        raise IntegrityError('ORIGINAL_MISSING_OR_UNSAFE')
    checksum=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(64*1024),b''):checksum.update(chunk)
    if checksum.hexdigest()!=digest:raise IntegrityError('ORIGINAL_INTEGRITY_ERROR')
    return path
