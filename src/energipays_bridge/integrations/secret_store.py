"""Integration secrets at rest and in the API (#17).

The REST bearer token, the HA long-lived token and the MQTT password used to sit in
the ``integrations.config`` JSON in plain text and were returned by the API. They are
now encrypted with the same AES-256-GCM machine key as the Energipays password
(``store/credentials.py``) and never leave the bridge:

- stored as ``enc:v1:<base64>``;
- the API returns them as ``""`` plus ``secrets_set: {field: true}``;
- on save, ``""`` (or a missing field) keeps the stored secret, ``null`` clears it.
"""
from __future__ import annotations

import logging

from ..store.credentials import _decrypt, _encrypt

log = logging.getLogger(__name__)

SECRET_FIELDS = ("auth_token", "token", "password")
_PREFIX = "enc:v1:"


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def seal(key: bytes, config: dict) -> dict:
    """Config as stored: every non-empty secret encrypted (idempotent)."""
    out = dict(config)
    for f in SECRET_FIELDS:
        v = out.get(f)
        if isinstance(v, str) and v and not is_encrypted(v):
            out[f] = _PREFIX + _encrypt(key, v)
    return out


def unseal(key: bytes, config: dict) -> dict:
    """Config for the pollers: secrets in plain text, only in memory."""
    out = dict(config)
    for f in SECRET_FIELDS:
        v = out.get(f)
        if is_encrypted(v):
            try:
                out[f] = _decrypt(key, v[len(_PREFIX):])
            except Exception:
                # Wrong or regenerated machine key: the secret is lost, not leaked.
                log.error("integration secret %r could not be decrypted — re-enter it", f)
                out[f] = ""
    return out


def redact(config: dict) -> tuple[dict, dict[str, bool]]:
    """Config for the API: secrets blanked, plus which ones are set."""
    out = dict(config)
    set_: dict[str, bool] = {}
    for f in SECRET_FIELDS:
        if f in out:
            set_[f] = bool(out[f])
            out[f] = ""
    return out, set_


def merge(stored: dict, incoming: dict) -> dict:
    """Apply an API update: blank or missing keeps the stored secret, None clears it."""
    out = dict(incoming)
    for f in SECRET_FIELDS:
        v = incoming.get(f, "")
        if v is None:
            out[f] = ""
        elif v == "" and stored.get(f):
            out[f] = stored[f]
    return out


def scrub(text: str, plain_config: dict) -> str:
    """Remove secret values from an error message before it is shown or logged."""
    for f in SECRET_FIELDS:
        v = plain_config.get(f)
        if isinstance(v, str) and len(v) >= 4:
            text = text.replace(v, "***")
    return text


def scrub_own(poller: object, text: str) -> str:
    """:func:`scrub` with a poller's own secret attributes (same names as the config)."""
    return scrub(text, {f: getattr(poller, f, None) for f in SECRET_FIELDS})
