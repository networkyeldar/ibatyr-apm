"""Pin the publisher public key and obtain the installation activation request."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from ai_security import CONFIG_PATH
from license_manager import LicenseManager

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--public-key', type=Path)
parser.add_argument('--request-output', type=Path)
args = parser.parse_args()
directory = Path(os.environ.get('IBATYR_LICENSE_DIR', str(CONFIG_PATH.parent / '.ibatyr_license')))
manager = LicenseManager(directory)
if manager.state_error:
    raise SystemExit('Состояние лицензии повреждено. Восстановите его из резервной копии.')
if args.public_key:
    raw = args.public_key.expanduser().read_bytes()
    try:
        key = serialization.load_pem_public_key(raw)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError()
    except ValueError:
        raise SystemExit('Нужен публичный ключ Ed25519, не закрытый ключ.')
    raw = key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    if manager.public_path.exists() and manager.public_path.read_bytes() != raw:
        raise SystemExit('Другой публичный ключ уже установлен. Автоматическая замена запрещена.')
    manager.public_path.write_bytes(raw)
    os.chmod(manager.public_path, 0o644)
    print('Публичный ключ издателя установлен. SHA-256:', hashlib.sha256(raw).hexdigest())
request = {'schema_version': 1, 'product': 'iBatyr APM', 'installation_id': manager.state['installation_id']}
if args.request_output:
    args.request_output.expanduser().write_text(json.dumps(request, indent=2))
    print('Запрос активации:', args.request_output)
print(json.dumps(request, indent=2))
