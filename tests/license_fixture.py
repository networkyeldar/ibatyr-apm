import base64
import json
import time
from uuid import uuid4
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from license_core import canonical


def provision(manager, **overrides):
    key = Ed25519PrivateKey.generate()
    manager.public_path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return key, document(manager, key, **overrides)


def document(manager, key, **overrides):
    now = int(time.time())
    payload = dict(schema_version=1, product='iBatyr APM', license_id=str(uuid4()),
        installation_id=manager.state['installation_id'], customer='Test customer', edition='trial',
        issued_at=now-10, not_before=now-10, expires_at=now+86400, grace_days=0, features=['ai_analysis'])
    payload.update(overrides)
    return json.dumps({'payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()})
