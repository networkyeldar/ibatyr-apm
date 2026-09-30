"""Owner-only offline issuer. Never deliver your private key to customers."""
import argparse
import base64
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys
from uuid import uuid4, UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shell'))
from license_core import canonical, LicensePayload


def new_file(path, content, mode):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    keys = sub.add_parser('keygen')
    keys.add_argument('--directory', required=True, type=Path)
    issue = sub.add_parser('issue')
    issue.add_argument('--private-key', required=True, type=Path)
    install = issue.add_mutually_exclusive_group(required=True)
    install.add_argument('--request', type=Path)
    install.add_argument('--installation-id')
    issue.add_argument('--customer', required=True)
    issue.add_argument('--edition', choices=['trial', 'professional', 'enterprise'], required=True)
    issue.add_argument('--days', type=int)
    issue.add_argument('--grace-days', type=int, default=0, choices=range(8))
    issue.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'keygen':
        directory = args.directory.expanduser()
        if any((directory/name).exists() for name in ['private.pem', 'public.pem']):
            parser.error('Ключи уже существуют: перезапись запрещена.')
        phrase = getpass.getpass('Пароль закрытого ключа (минимум 12 символов): ')
        if len(phrase) < 12 or phrase != getpass.getpass('Повторите пароль: '):
            parser.error('Пароль короткий или не совпадает.')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        new_file(directory/'private.pem', key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(phrase.encode())), 0o600)
        new_file(directory/'public.pem', key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo), 0o644)
        print('Ключи созданы. private.pem храните только у издателя, с резервной копией.')
        return
    installation_id = args.installation_id
    if args.request:
        req = json.loads(args.request.expanduser().read_text())
        if req.get('product') != 'iBatyr APM' or req.get('schema_version') != 1:
            parser.error('Неизвестный формат запроса активации')
        installation_id = req['installation_id']
    UUID(installation_id)
    days = args.days if args.days is not None else (30 if args.edition == 'trial' else 365)
    if not 1 <= days <= 3660:
        parser.error('Срок должен быть от 1 до 3660 дней')
    now = int(datetime.now(timezone.utc).timestamp())
    payload = LicensePayload(schema_version=1, product='iBatyr APM', license_id=str(uuid4()), installation_id=installation_id,
        customer=args.customer, edition=args.edition, issued_at=now, not_before=now, expires_at=now+days*86400,
        grace_days=args.grace_days, features=['ai_analysis']).model_dump()
    phrase = getpass.getpass('Пароль закрытого ключа: ')
    key = serialization.load_pem_private_key(args.private_key.expanduser().read_bytes(), password=phrase.encode())
    if not isinstance(key, Ed25519PrivateKey):
        parser.error('Требуется ключ Ed25519')
    signature = base64.b64encode(key.sign(canonical(payload))).decode()
    document = json.dumps({'payload': payload, 'signature': signature}, ensure_ascii=False, indent=2)
    new_file(args.output, document.encode(), 0o600)
    print(f'Лицензия {args.edition}, срок {days} дней, создана: {args.output}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit('Не удалось выпустить лицензию. Проверьте параметры, пароль ключа и пути. Существующие файлы не перезаписываются.') from None
