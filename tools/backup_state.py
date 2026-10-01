#!/usr/bin/env python3
"""Back up users, AI settings, licence identity and deployment configuration together."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile

SERVICE = 'ibatyr-apm.service'


def read_paths(env_file):
    # Installer writes KEY="value". Never execute/source a file containing secrets.
    values = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if sep and key in ('SW_AI_CONFIG', 'IBATYR_LICENSE_DIR'):
            value = value.strip()
            if value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            values[key] = value
    if not values.get('SW_AI_CONFIG'):
        raise ValueError('В EnvironmentFile не найден SW_AI_CONFIG; укажите --settings и --license-dir.')
    settings = Path(values['SW_AI_CONFIG'])
    license_dir = Path(values.get('IBATYR_LICENSE_DIR', str(settings.parent / '.ibatyr_license')))
    if not settings.is_absolute() or not license_dir.is_absolute():
        raise ValueError('Пути состояния должны быть абсолютными.')
    return settings, license_dir


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Ожидался обычный файл: {path}')


def snapshot(settings, license_dir, destination, extras=(), allow_missing_users=False):
    """Caller must stop the shell to make the multi-file snapshot consistent."""
    regular(settings)
    regular(license_dir / 'state.json')
    users = settings.with_name('users.sqlite3')
    if not users.exists() and not allow_missing_users:
        raise ValueError('users.sqlite3 не найдена. Копия без пользователей запрещена по умолчанию.')
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    if destination.is_symlink() or destination.stat().st_uid != os.geteuid() or stat.S_IMODE(destination.stat().st_mode) & 0o077:
        raise ValueError('Каталог резервных копий должен иметь права 0700.')
    manifest = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'users_included': users.exists(), 'files': []}
    with tempfile.TemporaryDirectory(prefix='.ibatyr-state-', dir=destination) as tmp:
        stage = Path(tmp) / 'state'
        stage.mkdir(mode=0o700)

        def add(source, name, database=False):
            regular(source)
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if database:
                # The backup API includes committed WAL contents; never copy just the .db file.
                with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
                    with closing(sqlite3.connect(target)) as dst:
                        src.backup(dst)
                        if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                            raise ValueError('Проверка целостности users.sqlite3 не прошла.')
                        if not dst.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone():
                            raise ValueError('В копии users.sqlite3 отсутствует активный администратор.')
            else:
                shutil.copyfile(source, target)
            target.chmod(0o600)
            info = source.stat()
            manifest['files'].append({'archive_path': name, 'original_path': str(source.absolute()),
                                      'uid': info.st_uid, 'gid': info.st_gid,
                                      'mode': stat.S_IMODE(info.st_mode),
                                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})

        add(settings, 'settings.json')
        if users.exists():
            add(users, 'users.sqlite3', database=True)
        else:
            print('ВНИМАНИЕ: legacy-копия без users.sqlite3; это явно разрешено флагом.')
        if license_dir.is_symlink():
            raise ValueError('Каталог лицензии не должен быть символьной ссылкой.')
        for source in sorted(license_dir.rglob('*')):
            if source.is_symlink():
                raise ValueError('Символьная ссылка в каталоге лицензии.')
            if source.is_file():
                add(source, 'license/' + source.relative_to(license_dir).as_posix())
        for source in extras:
            if source.exists():
                add(source, 'deployment/' + source.absolute().as_posix().lstrip('/'))
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        pending = Path(tmp) / 'backup.tar.gz'
        with tarfile.open(pending, 'w:gz') as archive:
            archive.add(stage, arcname='state')
        pending.chmod(0o600)
        # Verify actual compressed archive before publishing it as a completed backup.
        with tarfile.open(pending, 'r:gz') as archive:
            for item in manifest['files']:
                with archive.extractfile('state/' + item['archive_path']) as stream:
                    if hashlib.sha256(stream.read()).hexdigest() != item['sha256']:
                        raise ValueError('Контрольная сумма резервной копии не совпала.')
        with pending.open('rb') as stream:
            os.fsync(stream.fileno())
        result = destination / ('ibatyr-state-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ') + '.tar.gz')
        os.replace(pending, result)
        fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('/etc/ibatyr-apm/shell.env'))
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--license-dir', type=Path)
    parser.add_argument('--destination', type=Path, default=Path('/var/backups/ibatyr-apm'))
    parser.add_argument('--already-stopped', action='store_true', help='Updater owns stop/start; verify service is inactive.')
    parser.add_argument('--allow-missing-users', action='store_true', help='Only for an installation predating user management.')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Запустите через sudo python3.')
    if bool(args.settings) != bool(args.license_dir):
        raise SystemExit('--settings и --license-dir указываются вместе.')
    settings, license_dir = ((args.settings, args.license_dir) if args.settings else read_paths(args.env_file))
    if not settings.is_absolute() or not license_dir.is_absolute():
        raise SystemExit('Нужны абсолютные пути.')
    regular(settings)
    regular(license_dir / 'state.json')
    extras = [args.env_file, Path('/etc/systemd/system/ibatyr-apm.service'),
              Path('/etc/nginx/conf.d/ibatyr-apm.conf')]
    extras += sorted(Path('/etc/systemd/system/ibatyr-apm.service.d').glob('*.conf'))
    def control(*cmd):
        return subprocess.run(['systemctl', *cmd], check=True, text=True, capture_output=True)
    active = control('show', SERVICE, '--property=ActiveState', '--value').stdout.strip()
    if active not in ('active', 'inactive') or (args.already_stopped and active != 'inactive'):
        raise SystemExit('Неожиданное состояние службы; резервное копирование отменено: ' + active)
    try:
        if active == 'active':
            control('stop', SERVICE)
        result = snapshot(settings, license_dir, args.destination, extras, args.allow_missing_users)
        print('Проверенная резервная копия:', result, flush=True)
    finally:
        if active == 'active':
            control('start', SERVICE)


if __name__ == '__main__':
    main()
