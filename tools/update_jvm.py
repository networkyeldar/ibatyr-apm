#!/usr/bin/env python3
"""Update an existing 0.5.0-rc1 shell; preserve configuration and roll back on failure."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.request import urlopen

SOURCE = Path(__file__).resolve().parents[1] / 'shell'
FILES = ('ai_routes.py', 'dashboard_routes.py', 'jvm_routes.py')


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def ready(port):
    deadline = time.monotonic()+35
    while time.monotonic() < deadline:
        try:
            with urlopen(f'http://127.0.0.1:{port}/ready', timeout=2) as r:
                body = json.load(r)
            if body.get('status') == 'ok' and body.get('oap') == 'reachable':
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('Оболочка/OAP не подтвердили готовность за 35 секунд')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8011)
    args=parser.parse_args()
    app=args.app_dir.resolve()
    if os.geteuid()!=0:
        raise SystemExit('Запустите через sudo python3')
    work=run('systemctl','show','ibatyr-apm.service','--property=WorkingDirectory','--value',capture_output=True,text=True).stdout.strip()
    if Path(work).resolve()!=app:
        raise SystemExit(f'Каталог службы отличается: {work}')
    if (app/'VERSION').exists() and (app/'VERSION').read_text().strip()!='0.5.0-rc1':
        raise SystemExit('Обновление рассчитано на 0.5.0-rc1')
    if not (app/'ai_web/index.html').is_file() or not (app/'ai_routes.py').is_file():
        raise SystemExit('Не найдены файлы установленной оболочки')
    ready(args.port)  # Do not overwrite an already unhealthy installation.
    with tempfile.TemporaryDirectory(prefix='ibatyr-jvm-stage-') as tmp:
        stage=Path(tmp)
        for name in FILES:
            shutil.copy2(SOURCE/name,stage/name)
        shutil.copytree(SOURCE/'ai_web',stage/'ai_web')
        run(app/'.venv/bin/python','-m','py_compile',*(stage/name for name in FILES))
        backup=app/('update-backup-jvm-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.mkdir(mode=0o700)
        existing=[]
        for name in FILES:
            if (app/name).exists():
                shutil.copy2(app/name,backup/name);existing.append(name)
        shutil.copytree(app/'ai_web',backup/'ai_web')
        print('Резервная копия:',backup,flush=True)
        try:
            run('systemctl','stop','ibatyr-apm.service')
            for name in FILES:
                shutil.copy2(stage/name,app/name)
                (app/name).chmod(0o644)
            shutil.rmtree(app/'ai_web')
            shutil.copytree(stage/'ai_web',app/'ai_web')
            for path in (app/'ai_web').rglob('*'):
                path.chmod(0o755 if path.is_dir() else 0o644)
            run('systemctl','start','ibatyr-apm.service')
            ready(args.port)
            with urlopen(f'http://127.0.0.1:{args.port}/ai/',timeout=5) as r:
                if 'v=5.0-jvm-1' not in r.read().decode():
                    raise RuntimeError('Сервер отдаёт другую версию интерфейса')
        except Exception:
            print('Обновление не прошло проверку. Возвращаем предыдущие файлы.',flush=True)
            run('systemctl','stop','ibatyr-apm.service')
            for name in FILES:
                if name in existing:shutil.copy2(backup/name,app/name)
                else:(app/name).unlink(missing_ok=True)
            if (app/'ai_web').exists():shutil.rmtree(app/'ai_web')
            shutil.copytree(backup/'ai_web',app/'ai_web')
            run('systemctl','start','ibatyr-apm.service')
            raise
    print('JVM установлен. Откройте /ai/#jvm. Настройки, лицензия, OAP и Elasticsearch сохранены.')


if __name__=='__main__':
    main()
