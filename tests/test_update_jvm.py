import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

spec=importlib.util.spec_from_file_location('update_jvm',Path(__file__).parents[1]/'tools/update_jvm.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)


@pytest.mark.parametrize('fail',[False,True])
def test_update_preserves_private_state_and_rolls_back(tmp_path,monkeypatch,fail):
    app=tmp_path/'app';source=tmp_path/'source'
    for root,content in [(app,'old'),(source,'v=5.0-jvm-1')]:
        (root/'ai_web').mkdir(parents=True)
        (root/'ai_web/index.html').write_text(content)
        for name in updater.FILES:
            if root==app and name=='jvm_routes.py':continue
            (root/name).write_text(content)
    (app/'settings.json').write_text('private settings')
    commands=[];checks=0
    def run(*args,**kwargs):
        commands.append(args)
        return SimpleNamespace(stdout=str(app))
    def ready(port):
        nonlocal checks
        checks+=1
        if fail and checks==2:raise RuntimeError('not ready')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'v=5.0-jvm-1'
    monkeypatch.setattr(updater,'SOURCE',source)
    monkeypatch.setattr(updater,'run',run)
    monkeypatch.setattr(updater,'ready',ready)
    monkeypatch.setattr(updater,'urlopen',lambda *a,**k:Response())
    monkeypatch.setattr(updater.os,'geteuid',lambda:0)
    monkeypatch.setattr(sys,'argv',['update_jvm.py','--app-dir',str(app)])
    if fail:
        with pytest.raises(RuntimeError):updater.main()
        assert (app/'ai_web/index.html').read_text()=='old'
        assert (app/'ai_routes.py').read_text()=='old'
        assert not (app/'jvm_routes.py').exists()
    else:
        updater.main()
        assert (app/'jvm_routes.py').read_text()=='v=5.0-jvm-1'
    assert (app/'settings.json').read_text()=='private settings'
    assert len(list(app.glob('update-backup-jvm-*')))==1
    assert ('systemctl','start','ibatyr-apm.service') in commands
    assert all('skywalking.service' not in cmd for cmd in commands)
