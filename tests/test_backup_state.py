import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

import backup_state
from ai_users import Users
from test_ai import make_config


def fixture_state(tmp_path):
    settings = tmp_path / 'settings.json'
    make_config(settings)
    # Exercise real user schema and legacy migration.
    users = Users(settings.with_name('users.sqlite3'), json.loads(settings.read_text()))
    license_dir = tmp_path / 'license'
    license_dir.mkdir()
    (license_dir / 'state.json').write_text('{"installation_id":"keep-this-uuid","last_seen":12345}')
    (license_dir / 'license.json').write_text('{"signed":"fixture"}')
    (license_dir / 'trusted-public.pem').write_text('public fixture')
    return settings, license_dir, users


def test_sqlite_wal_snapshot_can_be_restored_with_settings_and_license(tmp_path):
    settings, license_dir, users = fixture_state(tmp_path)
    # Keep WAL open and uncheckpointed, including a newly committed audit record.
    db = sqlite3.connect(settings.with_name('users.sqlite3'))
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO audit(time,actor,action,target) VALUES (1,'admin','backup-test','second-user')")
        db.commit()
        archive = backup_state.snapshot(settings, license_dir, tmp_path / 'backups')
        assert archive.stat().st_mode & 0o777 == 0o600
        restore = tmp_path / 'restore'
        restore.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(restore, filter='data')
        state = restore / 'state'
        assert (state / 'settings.json').read_bytes() == settings.read_bytes()
        for path in license_dir.iterdir():
            assert (state / 'license' / path.name).read_bytes() == path.read_bytes()
        with sqlite3.connect(state / 'users.sqlite3') as restored:
            assert restored.execute('PRAGMA integrity_check').fetchone() == ('ok',)
            assert restored.execute("SELECT target FROM audit WHERE action='backup-test'").fetchone() == ('second-user',)
            assert restored.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone() == (1,)
        assert json.loads((state / 'manifest.json').read_text())['users_included'] is True
        assert not list((tmp_path / 'backups').glob('.ibatyr-state-*'))
    finally:
        db.close()


@pytest.mark.parametrize('failure', ['missing-users', 'corrupt-users', 'missing-license', 'symlink', 'open-directory'])
def test_invalid_backup_never_publishes_archive(tmp_path, failure):
    settings, license_dir, _ = fixture_state(tmp_path)
    db = settings.with_name('users.sqlite3')
    destination = tmp_path / 'backups'
    if failure == 'missing-users': db.unlink()
    elif failure == 'corrupt-users': db.write_bytes(b'not sqlite')
    elif failure == 'missing-license': (license_dir / 'state.json').unlink()
    elif failure == 'symlink': (license_dir / 'other').symlink_to(settings)
    else:
        destination.mkdir(); destination.chmod(0o755)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        backup_state.snapshot(settings, license_dir, destination)
    assert not list(destination.glob('*.tar.gz'))


def test_env_paths_are_read_not_executed(tmp_path):
    env = tmp_path / 'shell.env'
    env.write_text('SW_AI_CONFIG="/var/lib/ibatyr-apm/settings.json"\nIBATYR_LICENSE_DIR="/var/lib/ibatyr-apm/license"\nIGNORED=$(false)\n')
    assert backup_state.read_paths(env) == (Path('/var/lib/ibatyr-apm/settings.json'), Path('/var/lib/ibatyr-apm/license'))


def test_service_restarts_if_snapshot_fails(tmp_path, monkeypatch):
    settings, license_dir, _ = fixture_state(tmp_path)
    commands = []
    from types import SimpleNamespace
    import sys
    monkeypatch.setattr(backup_state.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(backup_state.subprocess, 'run', lambda args, **kw: commands.append(args) or SimpleNamespace(stdout='active\n'))
    def failed(*args): raise ValueError('disk unavailable')
    monkeypatch.setattr(backup_state, 'snapshot', failed)
    monkeypatch.setattr(sys, 'argv', ['backup_state.py', '--settings', str(settings), '--license-dir', str(license_dir)])
    with pytest.raises(ValueError, match='disk unavailable'): backup_state.main()
    assert commands[-2:] == [['systemctl', 'stop', 'ibatyr-apm.service'], ['systemctl', 'start', 'ibatyr-apm.service']]
