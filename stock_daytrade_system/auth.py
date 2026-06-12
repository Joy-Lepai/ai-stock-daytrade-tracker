from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password_hash: str
    salt: str
    iterations: int


def load_auth_config(path: Path) -> AuthConfig:
    env_username = os.getenv("STOCK_WEB_USERNAME")
    env_password = os.getenv("STOCK_WEB_PASSWORD")
    if env_username and env_password:
        iterations = int(os.getenv("STOCK_WEB_PASSWORD_ITERATIONS", "200000"))
        salt = os.getenv("STOCK_WEB_PASSWORD_SALT", "ai-stock-system-env")
        password_hash = _hash_password(env_password, salt, iterations)
        return AuthConfig(
            username=env_username,
            password_hash=password_hash,
            salt=salt,
            iterations=iterations,
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AuthConfig(
        username=raw["username"],
        password_hash=raw["password_hash"],
        salt=raw["salt"],
        iterations=int(raw["iterations"]),
    )


def verify_password(password: str, config: AuthConfig) -> bool:
    digest = _hash_password(password, config.salt, config.iterations)
    return hmac.compare_digest(digest, config.password_hash)


def _hash_password(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
