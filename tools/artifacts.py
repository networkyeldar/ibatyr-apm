"""Verify upstream bytes before extraction. Never execute an unverified archive."""
import hashlib,json,os,tarfile,tempfile,shutil
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]

def manifest():return json.loads((ROOT/'versions.json').read_text())

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha512').hexdigest()

def acquire(component, supplied=None):
    item=manifest()[component]
    path=Path(supplied) if supplied else ROOT/'vendor'/item['filename']
    if not path.exists():
        if supplied:raise RuntimeError(f'Не найден архив: {path}')
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='download-')
        try:
            with os.fdopen(fd,'wb') as f,urlopen(item['url'],timeout=120) as response:
                shutil.copyfileobj(response,f,1024*1024)
            if digest(tmp)!=item['sha512']:raise RuntimeError('SHA-512 загруженного файла не совпал')
            os.replace(tmp,path)
        finally:Path(tmp).unlink(missing_ok=True)
    if digest(path)!=item['sha512']:raise RuntimeError(f'SHA-512 не совпал: {path}')
    return path

def extract(archive,destination):
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive) as tar:
        members=tar.getmembers()
        if sum(m.size for m in members)>2*1024**3:raise RuntimeError('Архив больше разрешённых 2 ГБ')
        for m in members:
            target=(destination/m.name).resolve()
            if not target.is_relative_to(destination) or not (m.isfile() or m.isdir()):
                raise RuntimeError('Небезопасная запись архива: '+m.name)
        tar.extractall(destination,members=members,filter='data')
    return destination
