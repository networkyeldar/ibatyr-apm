"""Persistent shell accounts. This database does not manage SkyWalking/OAP access."""
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from fastapi import HTTPException

PUBLIC = ('id', 'username', 'role', 'active', 'created_at')

def public(row):
    return {k: bool(row[k]) if k == 'active' else row[k] for k in PUBLIC}


class Users:
    def __init__(self, path, legacy):
        self.path = path
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            new = False
        else:
            os.close(fd);new = True
        os.chmod(path, 0o600)
        with self.db() as db:
            if new:
                db.executescript('''
                    CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        role TEXT NOT NULL CHECK(role IN ('admin','user')), active INTEGER NOT NULL,
                        salt TEXT NOT NULL, password_hash TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL);
                    CREATE TABLE audit (id INTEGER PRIMARY KEY, time INTEGER NOT NULL,
                        actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
                ''')
                db.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?,?)',
                           (secrets.token_hex(16), legacy['username'], 'admin', 1,
                            legacy['password_salt'], legacy['password_hash'], 1, int(time.time())))
                db.execute('INSERT INTO audit(time,actor,action,target) VALUES(?,?,?,?)',
                           (int(time.time()), 'migration', 'import_admin', legacy['username']))
            if not db.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone():
                raise RuntimeError('В базе пользователей нет активного администратора. Восстановите резервную копию базы.')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, identity, by_name=False):
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE '+('username' if by_name else 'id')+'=?', (identity,)).fetchone()
            return dict(row) if row else None

    def list(self):
        with self.db() as db:
            return [public(row) for row in db.execute('SELECT * FROM users ORDER BY created_at, username')]

    def events(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT time,actor,action,target FROM audit ORDER BY id DESC LIMIT 100')]

    @staticmethod
    def actor(db, actor_id):
        actor = db.execute('SELECT * FROM users WHERE id=?', (actor_id,)).fetchone()
        if not actor or not actor['active'] or actor['role'] != 'admin':
            raise HTTPException(403, 'Требуются права администратора')
        return actor

    @staticmethod
    def audit(db, actor, action, target):
        db.execute('INSERT INTO audit(time,actor,action,target) VALUES(?,?,?,?)',
                   (int(time.time()), actor['username'], action, target))
        db.execute('DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 1000)')

    def create(self, actor_id, username, role, salt, digest):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = self.actor(db, actor_id)
            if db.execute('SELECT count(*) FROM users').fetchone()[0] >= 200:
                raise HTTPException(409, 'Достигнут лимит 200 учётных записей')
            uid = secrets.token_hex(16)
            try:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)',
                           (uid, username, role, 1, salt, digest, 1, int(time.time())))
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'Этот логин уже существует')
            self.audit(db, actor, 'create', username)
            return public(db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())

    def update(self, actor_id, uid, role=None, active=None, credentials=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = self.actor(db, actor_id)
            row = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            if not row:
                raise HTTPException(404, 'Пользователь не найден')
            role = row['role'] if role is None else role
            active = row['active'] if active is None else int(active)
            if uid == actor_id and (not active or role != 'admin'):
                raise HTTPException(409, 'Нельзя заблокировать себя или изменить собственную роль')
            if row['role'] == 'admin' and row['active'] and (not active or role != 'admin'):
                if db.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                    raise HTTPException(409, 'Нельзя отключить последнего администратора')
            salt, digest = credentials or (row['salt'], row['password_hash'])
            db.execute('UPDATE users SET role=?,active=?,salt=?,password_hash=?,version=version+1 WHERE id=?',
                       (role, active, salt, digest, uid))
            self.audit(db, actor, 'reset_password' if credentials else 'update_access', row['username'])
            return public(db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
