"""Шифрование HLS и создание отпечатков для защиты от повторов."""

import hashlib
import hmac

from cryptography.fernet import Fernet

from hls_key_control.config import load_hls_security_settings


class HlsProtector:
    """Шифрует HLS для БД и создаёт безопасные отпечатки."""

    def __init__(self) -> None:
        settings = load_hls_security_settings()

        try:
            self._fernet = Fernet(
                settings.encryption_key.encode("ascii")
            )
        except ValueError as error:
            raise RuntimeError(
                "HLS_ENCRYPTION_KEY содержит некорректное значение."
            ) from error

        self._fingerprint_secret = settings.fingerprint_secret

    def encrypt(self, hls_password: str) -> bytes:
        """Шифрует HLS-пароль для хранения в SQL Server."""
        return self._fernet.encrypt(hls_password.encode("utf-8"))

    def decrypt(self, encrypted_value: bytes) -> str:
        """Расшифровывает HLS только для выполнения операции с ПУ."""
        return self._fernet.decrypt(encrypted_value).decode("utf-8")

    def create_fingerprint(self, hls_password: str) -> str:
        """Создаёт постоянный отпечаток без хранения открытого HLS."""
        return hmac.new(
            key=self._fingerprint_secret,
            msg=hls_password.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()