"""Identity helpers. Raw UCIDs are never stored; pilots are keyed by a peppered HMAC."""

from __future__ import annotations

import hashlib
import hmac
import secrets

MIN_PEPPER_BYTES = 32


def normalize_ucid(ucid: str) -> str:
    return ucid.strip().lower()


def pilot_id(pepper: bytes, ucid: str) -> str:
    return hmac.new(pepper, normalize_ucid(ucid).encode("utf-8"), hashlib.sha256).hexdigest()


def check_pepper(pepper: bytes) -> None:
    if len(pepper.strip()) < MIN_PEPPER_BYTES:
        raise RuntimeError(
            f"WINGMAN_PEPPER must be at least {MIN_PEPPER_BYTES} random bytes "
            "(for example: openssl rand -hex 32). It keys every pilot identity."
        )


def new_server_key() -> str:
    return "wsk_" + secrets.token_hex(16)


def new_fleet_key() -> str:
    return "wfk_" + secrets.token_hex(16)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def ordered_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)
