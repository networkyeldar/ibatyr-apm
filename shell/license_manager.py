import hashlib
import json
import math
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ai_security import write_config
from license_core import verify_document


class Activation(BaseModel):
    document: str = Field(min_length=1, max_length=20000)


class LicenseManager:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_path = self.directory / 'state.json'
        self.public_path = self.directory / 'trusted-public.pem'
        self.license_path = self.directory / 'license.json'
        self.clock_start, self.monotonic_start = time.time(), time.monotonic()
        self.last_write = 0
        self.state_error = False
        try:
            if self.state_path.exists():
                self.state = json.loads(self.state_path.read_text())
                UUID(self.state['installation_id'])
                if not isinstance(self.state['last_seen'], (int, float)) or not math.isfinite(self.state['last_seen']):
                    raise ValueError()
                if not isinstance(self.state.get('events', []), list):
                    raise ValueError()
            else:
                self.state = {'installation_id': str(uuid4()), 'last_seen': int(time.time()), 'events': []}
                write_config(self.state_path, self.state)
        except (ValueError, OSError, KeyError, TypeError):
            self.state_error = True
            self.state = {'installation_id': None, 'last_seen': 0, 'events': []}

    def now(self):
        wall = time.time()
        expected = self.clock_start + time.monotonic() - self.monotonic_start
        if self.state_error:
            return wall, 'state_error'
        if wall + 300 < max(self.state['last_seen'], expected):
            return wall, 'clock_error'
        now = max(wall, expected, self.state['last_seen'])
        if time.monotonic()-self.last_write > 30:
            self.state['last_seen'] = int(now)
            try:
                write_config(self.state_path, self.state)
            except OSError:
                return now, 'state_error'
            self.last_write = time.monotonic()
        return now, None

    def public_status(self):
        now, problem = self.now()
        result = {'product': 'iBatyr APM', 'agent_product': 'iBatyr APM agent',
                  'installation_id': self.state['installation_id'], 'status': problem or 'unlicensed',
                  'ai_enabled': False, 'features': [], 'license': None, 'public_key_fingerprint': None,
                  'server_time': int(now), 'message': 'Загрузите лицензию для AI-анализа. Метрики и трассировки доступны.'}
        if problem:
            result['message'] = 'Проверьте время сервера.' if problem == 'clock_error' else 'Ошибка состояния лицензии. Обратитесь к администратору сервера.'
            return result
        try:
            public = self.public_path.read_bytes()
            result['public_key_fingerprint'] = hashlib.sha256(public).hexdigest()
        except OSError:
            result.update(status='not_configured', message='Администратор должен установить публичный ключ издателя.')
            return result
        if not self.license_path.exists():
            return result
        try:
            p = verify_document(self.license_path.read_text(), public)
        except (ValueError, OSError):
            result.update(status='invalid', message='Файл лицензии повреждён или подписан другим издателем.')
            return result
        if p['installation_id'] != self.state['installation_id']:
            result.update(status='wrong_installation', message='Лицензия выпущена для другой установки.')
            return result
        result['license'] = p
        expires = p['expires_at']
        remaining = max(0, math.ceil((expires-now)/86400))
        result['days_remaining'] = remaining
        if now < max(p['not_before'], p['issued_at']):
            result.update(status='not_yet_valid', message='Срок действия лицензии ещё не начался.')
        elif now < expires:
            result.update(status='active', message=f'Лицензия активна. Осталось дней: {remaining}.')
        elif now < expires + p['grace_days']*86400:
            result.update(status='grace', message='Льготный период: продлите лицензию.', days_remaining=0)
        else:
            result.update(status='expired', message='Лицензия истекла. AI-анализ отключён; метрики, трассировки и экспорт доступны.')
        if result['status'] in ('active', 'grace'):
            result['features'] = p['features']
            result['ai_enabled'] = 'ai_analysis' in p['features']
        return result

    def require_ai(self):
        status = self.public_status()
        if not status['ai_enabled']:
            raise HTTPException(403, status['message'] if status['status'] not in ('active', 'grace') else 'AI-анализ не включён в лицензию.')
        return hashlib.sha256(self.license_path.read_bytes()).hexdigest()

    def activate(self, document):
        now, problem = self.now()
        if problem:
            raise HTTPException(409, 'Сначала исправьте состояние или время сервера.')
        try:
            p = verify_document(document, self.public_path.read_bytes())
        except OSError:
            raise HTTPException(409, 'Не установлен публичный ключ издателя.')
        except ValueError as error:
            raise HTTPException(422, str(error))
        if p['installation_id'] != self.state['installation_id']:
            raise HTTPException(422, 'Лицензия выпущена для другой установки.')
        if now >= p['expires_at'] + p['grace_days']*86400:
            raise HTTPException(422, 'Срок этой лицензии уже истёк.')
        if now < max(p['not_before'], p['issued_at']):
            raise HTTPException(422, 'Срок этой лицензии ещё не начался.')
        # Verify entirely before replacing a working license; do not restart a trial on import.
        write_config(self.license_path, json.loads(document))
        self.state['events'] = (self.state.get('events', []) + [{'time': int(now), 'event': 'activated', 'license_id': p['license_id'], 'edition': p['edition']}])[-100:]
        write_config(self.state_path, self.state)
        return self.public_status()

    def register(self, app):
        @app.get('/api/ai/license')
        async def status():
            return self.public_status()

        @app.get('/api/ai/license/request')
        async def activation_request():
            return {'schema_version': 1, 'product': 'iBatyr APM', 'installation_id': self.state['installation_id']}

        @app.get('/api/ai/license/history')
        async def history():
            return {'events': self.state.get('events', [])}

        @app.post('/api/ai/license/activate')
        async def activate(body: Activation):
            return self.activate(body.document)
