"""Encryption of stored project secrets (Fernet: AES-128-CBC + HMAC-SHA256).

The key comes from SECRET_KEY or DATA_DIR/secret.key, which is generated on first use. Losing
the key makes stored secrets unreadable; projects then report ``credentials_readable=false``
and the user re-enters them. Back up the key file together with the database.
"""

import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.settings import ConfigError, Settings

log = logging.getLogger("app.storage")

KEY_FILE_MODE = 0o600


class SecretsUnreadable(Exception):
    """Stored secrets cannot be decrypted with the current key."""


class SecretBox:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, secrets: dict[str, str]) -> bytes:
        return self._fernet.encrypt(json.dumps(secrets, sort_keys=True).encode())

    def decrypt(self, blob: bytes) -> dict[str, str]:
        try:
            data = json.loads(self._fernet.decrypt(blob))
        except InvalidToken, ValueError:
            raise SecretsUnreadable from None
        return {str(k): str(v) for k, v in data.items()}


def _read_or_create_key(path: Path) -> bytes:
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, KEY_FILE_MODE)
    except FileExistsError:  # created concurrently by another process
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(key + b"\n")
    log.warning("generated %s; back it up with the database or stored secrets are lost", path)
    return key


def load_secret_box(settings: Settings) -> SecretBox:
    if settings.secret_key is not None:
        key, origin = settings.secret_key.get_secret_value().encode(), "SECRET_KEY"
    else:
        key, origin = _read_or_create_key(settings.secret_key_path), str(settings.secret_key_path)
    try:
        return SecretBox(key)
    except ValueError:
        raise ConfigError(
            f"Invalid configuration:\n  {origin}: expected a Fernet key "
            "(32 url-safe base64-encoded bytes)"
        ) from None
