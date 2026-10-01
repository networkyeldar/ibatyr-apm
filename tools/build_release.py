"""Build customer packages with allowlists; issuer and private state never included."""
import argparse,hashlib,json,shutil,tarfile,tempfile
from pathlib import Path
from artifacts import ROOT,manifest,acquire
p=argparse.ArgumentParser();p.add_argument('--with-vendor',action='store_true');a=p.parse_args()
version=manifest()['product_version'];out=ROOT/'packages';out.mkdir(exist_ok=True)

def copy(source,dest):
    source=ROOT/source
    if source.is_dir():shutil.copytree(source,dest,ignore=shutil.ignore_patterns('__pycache__','.venv','.*','*.pem','*license.json','*settings.json'))
    else:shutil.copy2(source,dest)

for component in ['shell','server','agent']:
    path=out/f'ibatyr-apm-{component}-{version}.tar.gz'
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)/f'ibatyr-apm-{component}-{version}';root.mkdir();(root/'tools').mkdir()
        for name in ['install.py','versions.json','VERSION','README.md','LICENSE','legal','docs','branding','config']:
            copy(name,root/name)
        copy('tools/artifacts.py',root/'tools/artifacts.py')
        copy('tools/doctor.py',root/'tools/doctor.py')
        copy('tools/elastic_install.py',root/'tools/elastic_install.py')
        if component=='shell':
            copy('shell',root/'shell')
            copy('tools/update_workspace.py',root/'tools/update_workspace.py')
            copy('tools/update_jvm.py',root/'tools/update_jvm.py')
        elif a.with_vendor:
            archive=acquire(component);(root/'vendor').mkdir();shutil.copy2(archive,root/'vendor'/archive.name)
        with tarfile.open(path,'w:gz') as tar:tar.add(root,arcname=root.name)
    print(path)
paths=sorted(out.glob(f'ibatyr-apm-*-{version}.tar.gz'))
(out/'SHA256SUMS').write_text(''.join(hashlib.sha256(x.read_bytes()).hexdigest()+'  '+x.name+'\n' for x in paths))
