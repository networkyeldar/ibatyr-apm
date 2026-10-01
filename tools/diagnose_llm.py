#!/usr/bin/env python3
"""Diagnose saved local Chat Completions profile without printing keys or provider bodies."""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx


def probe(client, method, url, headers, payload=None):
    start = time.monotonic()
    try:
        with client.stream(method, url, headers=headers, **({'json': payload} if payload else {})) as response:
            code = response.status_code
            print(f'HTTP {code}; {time.monotonic() - start:.2f} с до заголовков')
            if code != 200:
                messages = {401: 'Проверьте Bearer API key.', 403: 'Сервер отказал в доступе.',
                            404: 'Путь или модель не найдены; сравните Base URL и ID из /models.',
                            400: 'Запрос отклонён: проверьте модель, chat template и параметр лимита токенов.',
                            422: 'Запрос отклонён: проверьте формат/параметры API.',
                            429: 'Очередь, лимит запросов или квота сервера.'}
                print(messages.get(code, 'HTTP-сервер доступен; исследуйте его журналы.'))
                return None
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    print('Ответ больше 2 MB; чтение остановлено.'); return None
        print(f'Ответ получен полностью за {time.monotonic() - start:.2f} с')
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            print('Ответ не JSON (для /health это допустимо).'); return None
    except httpx.ConnectTimeout:
        print('CONNECT_TIMEOUT: соединение не установлено за 5 с. Проверьте маршрут, firewall и слушающий порт.')
    except httpx.ReadTimeout:
        print('READ_TIMEOUT: нет данных в течение 100 с. Проверьте очередь, загрузку модели/GPU и журналы LLM.')
    except httpx.WriteTimeout:
        print('WRITE_TIMEOUT: не удалось передать запрос вовремя.')
    except httpx.HTTPError as error:
        # Exception strings can include credentials; emit only fixed class name.
        print(type(error).__name__ + ': проверьте сеть, TLS и сервер LLM; секреты и тело ответа не выводятся.')
    return None


def diagnose(profile, client, chat=False):
    base = profile.get('base_url', '').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Некорректный Base URL; исправьте профиль локальной модели.')
    model = profile.get('model', '')
    parameter = profile.get('token_parameter', 'max_tokens')
    if parameter not in ('max_tokens', 'max_completion_tokens') or not model:
        raise ValueError('Не настроены модель или параметр лимита токенов.')
    headers = {'Content-Type': 'application/json'}
    if profile.get('auth_mode') == 'bearer':
        if not profile.get('api_key'):
            raise ValueError('В локальном профиле отсутствует сохранённый API key.')
        headers['Authorization'] = 'Bearer ' + profile['api_key']
    print('Локальный профиль:', base)
    print('Модель:', model, '| параметр:', parameter)
    print('1. Health (диагностический путь, может отсутствовать):')
    probe(client, 'GET', f'{parsed.scheme}://{parsed.netloc}/health', headers)
    print('2. Список моделей с сохранённой авторизацией:')
    data = probe(client, 'GET', base + '/models', headers)
    if isinstance(data, dict) and isinstance(data.get('data'), list):
        ids = [row.get('id') for row in data['data'] if isinstance(row, dict) and isinstance(row.get('id'), str)]
        print('ID моделей:', json.dumps(ids, ensure_ascii=False))
        print('Настроенная модель в списке:', 'ДА' if model in ids else 'НЕТ — выберите точный ID из списка.')
    if chat:
        print('3. Один Chat Completions запрос, лимит 128 токенов; без APM/SQL данных:')
        data = probe(client, 'POST', base + '/chat/completions', headers,
                     {'model': model, 'messages': [{'role': 'user', 'content': 'Reply with OK.'}],
                      'stream': False, parameter: 128})
        if isinstance(data, dict):
            choices = data.get('choices')
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                choice = choices[0]
                content = (choice.get('message') or {}).get('content') if isinstance(choice.get('message'), dict) else None
                reason = choice.get('finish_reason')
                print('Chat Completions: ответ получен; текст:', 'есть' if isinstance(content, str) and content.strip() else 'пустой')
                print('finish_reason:', reason if reason in ('stop', 'length', 'content_filter', 'tool_calls', None) else 'другое')
                print('Короткий тест не подтверждает скорость полного анализа. При length/пустом тексте лимита 128 может не хватить.')
            else:
                print('HTTP 200, но отсутствует ожидаемое поле choices; проверьте совместимость Chat Completions.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/var/lib/ibatyr-apm/settings.json'))
    parser.add_argument('--chat', action='store_true', help='Send one short inference request, no monitoring data')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        profile = config.get('providers', {}).get('local')
        if not isinstance(profile, dict):
            raise ValueError('Локальный профиль не найден; сначала сохраните его в «Подключения AI».')
        with httpx.Client(timeout=httpx.Timeout(100, connect=5), trust_env=False, follow_redirects=False) as client:
            diagnose(profile, client, args.chat)
    except (OSError, ValueError):
        raise SystemExit('Не удалось прочитать/проверить конфигурацию локального профиля. Проверьте --config, права и настройки; содержимое скрыто.')


if __name__ == '__main__':
    main()
