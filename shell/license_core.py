"""iBatyr commercial feature license format; Apache components are not gated."""
import base64
import json
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, model_validator

PRODUCT = 'iBatyr APM'


class LicensePayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1]
    product: Literal['iBatyr APM']
    license_id: str
    installation_id: str
    customer: str = Field(min_length=1, max_length=200)
    edition: Literal['trial', 'professional', 'enterprise']
    issued_at: int = Field(ge=0)
    not_before: int = Field(ge=0)
    expires_at: int = Field(gt=0)
    grace_days: int = Field(default=0, ge=0, le=7)
    features: list[Literal['ai_analysis']] = Field(max_length=1)

    @model_validator(mode='after')
    def dates(self):
        for value in (self.license_id, self.installation_id):
            if str(UUID(value)) != value:
                raise ValueError('Invalid UUID')
        if self.expires_at <= self.not_before or self.issued_at > self.expires_at:
            raise ValueError('Invalid license period')
        if self.edition == 'trial' and (self.grace_days or self.expires_at-self.not_before > 30*86400):
            raise ValueError('Trial must be at most 30 days, without grace')
        return self


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate field')
        value[key] = item
    return value


def verify_document(document, public_bytes):
    if not isinstance(document, str) or len(document.encode()) > 20000:
        raise ValueError('Некорректный размер лицензии')
    try:
        envelope = json.loads(document, object_pairs_hook=no_duplicates)
        if not isinstance(envelope, dict) or set(envelope) != {'payload', 'signature'}:
            raise ValueError()
        public = serialization.load_pem_public_key(public_bytes)
        if not isinstance(public, Ed25519PublicKey):
            raise ValueError()
        signature = base64.b64decode(envelope['signature'], validate=True)
        public.verify(signature, canonical(envelope['payload']))
        payload = LicensePayload.model_validate(envelope['payload'])
        return payload.model_dump()
    except (ValueError, TypeError, KeyError, InvalidSignature):
        raise ValueError('Недействительная подпись или формат лицензии') from None
