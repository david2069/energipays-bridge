"""
Secure credential storage.

Credentials are encrypted with AES-256-GCM using a random 32-byte key stored in
data/.secret_key (chmod 600). The ciphertext is stored in the app_config table.

Key file never leaves the server; DB file alone is not enough to recover credentials.
Env vars (ENERGIPAYS_EMAIL / ENERGIPAYS_PASSWORD) always take precedence.
"""
from __future__ import annotations

import base64
import logging
import os
import pathlib
import secrets
import stat

import aiosqlite
from Crypto.Cipher import AES

from .db import get_config, set_config

log = logging.getLogger(__name__)

_KEY_FILENAME = ".secret_key"


def _key_path(data_dir: pathlib.Path) -> pathlib.Path:
    return data_dir / _KEY_FILENAME


def _load_or_create_key(data_dir: pathlib.Path) -> bytes:
    path = _key_path(data_dir)
    if path.exists():
        raw = path.read_bytes()
        if len(raw) == 32:
            log.debug("encryption: loaded existing 256-bit machine key from %s", path)
            return raw
        log.warning("encryption: key file %s has wrong length (%d bytes) — regenerating", path, len(raw))
    log.info("encryption: generating new 256-bit machine key at %s", path)
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600
    log.info("encryption: key file written (chmod 600)")
    return key


def _encrypt(key: bytes, plaintext: str) -> str:
    """AES-256-GCM encrypt; return base64(nonce + tag + ciphertext)."""
    nonce = secrets.token_bytes(16)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode())
    blob = nonce + tag + ciphertext
    encoded = base64.b64encode(blob).decode()
    log.debug("encryption: AES-256-GCM encrypt OK (plaintext %d chars → %d chars ciphertext)", len(plaintext), len(encoded))
    return encoded


def _decrypt(key: bytes, encoded: str) -> str:
    blob = base64.b64decode(encoded)
    nonce, tag, ciphertext = blob[:16], blob[16:32], blob[32:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = cipher.decrypt_and_verify(ciphertext, tag).decode()
    log.debug("encryption: AES-256-GCM decrypt + MAC verify OK")
    return plaintext


async def save_credentials(db: aiosqlite.Connection, data_dir: pathlib.Path,
                            email: str, password: str) -> None:
    log.info("credentials: saving credentials for %s", email)
    key = _load_or_create_key(data_dir)
    await set_config(db, "cred_email", email)
    await set_config(db, "cred_password_enc", _encrypt(key, password))
    log.info("credentials: saved (email in plaintext, password AES-256-GCM encrypted)")


async def load_credentials(db: aiosqlite.Connection,
                            data_dir: pathlib.Path) -> tuple[str, str]:
    """Return (email, password). Empty strings if not stored."""
    env_email = os.environ.get("ENERGIPAYS_EMAIL", "")
    env_password = os.environ.get("ENERGIPAYS_PASSWORD", "")
    if env_email and env_password:
        log.info("credentials: loaded from environment variables (ENERGIPAYS_EMAIL / ENERGIPAYS_PASSWORD)")
        return env_email, env_password

    email = await get_config(db, "cred_email")
    enc_pw = await get_config(db, "cred_password_enc")
    if not email or not enc_pw:
        log.info("credentials: none found in DB or environment — setup required")
        return "", ""
    try:
        key = _load_or_create_key(data_dir)
        password = _decrypt(key, enc_pw)
        log.info("credentials: loaded from DB for %s (password decrypted successfully)", email)
        return email, password
    except Exception as exc:
        log.error("credentials: decryption failed — stored credentials may be corrupt: %s", exc)
        return "", ""


async def has_credentials(db: aiosqlite.Connection, data_dir: pathlib.Path) -> bool:
    email, pw = await load_credentials(db, data_dir)
    return bool(email and pw)
