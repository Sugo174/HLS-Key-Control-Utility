"""Генерация HLS-паролей и их безопасных отпечатков."""

import hashlib
import hmac
import secrets


HLS_LENGTH = 16
HLS_MAX_VALUE = 10**HLS_LENGTH


def generate_hls_password() -> str:
    """Создаёт новый 16-значный HLS-пароль, отличный от заводского."""

    number = secrets.randbelow(HLS_MAX_VALUE - 1) + 1
    return f"{number:0{HLS_LENGTH}d}"


def create_hls_fingerprint(
    hls_password: str,
    fingerprint_secret: bytes,
) -> str:
    """Создаёт неизменяемый отпечаток HLS для поиска повторов в базе."""

    return hmac.new(
        key=fingerprint_secret,
        msg=hls_password.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()