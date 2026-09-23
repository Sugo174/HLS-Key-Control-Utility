"""Загрузка локальных настроек подключения к SQL Server."""

import os
import base64
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Настройки подключения к служебной базе HLS."""

    odbc_driver: str
    server: str
    database: str
    user: str
    password: str
    encrypt: str
    trust_server_certificate: str

    def connection_string(self) -> str:
        """Формирует строку подключения, не выводя её в журнал."""
        return ";".join(
            [
                f"DRIVER={{{self.odbc_driver}}}",
                f"SERVER={self.server}",
                f"DATABASE={self.database}",
                f"UID={self.user}",
                f"PWD={self.password}",
                f"Encrypt={self.encrypt}",
                (
                    "TrustServerCertificate="
                    f"{self.trust_server_certificate}"
                ),
                "Connection Timeout=5",
            ]
        )


@dataclass(frozen=True, slots=True)
class HlsSecuritySettings:
    """Локальные секреты для защиты HLS-паролей."""

    encryption_key: str
    fingerprint_secret: bytes



def load_database_settings() -> DatabaseSettings:
    """Читает настройки из локального файла .env."""
    load_dotenv()

    required_names = (
        "HLS_DB_ODBC_DRIVER",
        "HLS_DB_SERVER",
        "HLS_DB_NAME",
        "HLS_DB_USER",
        "HLS_DB_PASSWORD",
        "HLS_DB_ENCRYPT",
        "HLS_DB_TRUST_SERVER_CERTIFICATE",
    )

    values = {name: os.getenv(name, "").strip() for name in required_names}
    missing_names = [name for name, value in values.items() if not value]

    if missing_names:
        missing_text = ", ".join(missing_names)
        raise RuntimeError(
            f"Не заполнены настройки SQL Server: {missing_text}."
        )

    return DatabaseSettings(
        odbc_driver=values["HLS_DB_ODBC_DRIVER"],
        server=values["HLS_DB_SERVER"],
        database=values["HLS_DB_NAME"],
        user=values["HLS_DB_USER"],
        password=values["HLS_DB_PASSWORD"],
        encrypt=values["HLS_DB_ENCRYPT"],
        trust_server_certificate=(
            values["HLS_DB_TRUST_SERVER_CERTIFICATE"]
        ),
    )


def load_hls_security_settings() -> HlsSecuritySettings:
    """Читает локальные секреты защиты HLS из файла .env."""
    load_dotenv()

    encryption_key = os.getenv("HLS_ENCRYPTION_KEY", "").strip()
    fingerprint_secret_text = os.getenv(
        "HLS_FINGERPRINT_SECRET",
        "",
    ).strip()

    if not encryption_key:
        raise RuntimeError("Не задан HLS_ENCRYPTION_KEY.")

    if not fingerprint_secret_text:
        raise RuntimeError("Не задан HLS_FINGERPRINT_SECRET.")

    try:
        fingerprint_secret = base64.urlsafe_b64decode(
            fingerprint_secret_text.encode("ascii")
        )
    except ValueError as error:
        raise RuntimeError(
            "HLS_FINGERPRINT_SECRET содержит некорректное значение."
        ) from error

    if len(fingerprint_secret) != 32:
        raise RuntimeError(
            "HLS_FINGERPRINT_SECRET должен содержать 32 байта."
        )

    return HlsSecuritySettings(
        encryption_key=encryption_key,
        fingerprint_secret=fingerprint_secret,
    )


@dataclass(frozen=True, slots=True)
class MeterConnectionSettings:
    """Настройки соединения с оптической головкой."""

    port: str
    baudrate: int
    timeout_seconds: float


def load_meter_connection_settings() -> MeterConnectionSettings:
    """Читает локальные настройки COM-порта из файла .env."""
    load_dotenv()

    port = os.getenv("HLS_METER_PORT", "").strip()
    baudrate_text = os.getenv("HLS_METER_BAUDRATE", "").strip()
    timeout_text = os.getenv(
        "HLS_METER_TIMEOUT_SECONDS",
        "",
    ).strip()

    if not port:
        raise RuntimeError("Не задан HLS_METER_PORT.")

    try:
        baudrate = int(baudrate_text)
        timeout_seconds = float(timeout_text)
    except ValueError as error:
        raise RuntimeError(
            "Некорректные настройки скорости или тайм-аута COM-порта."
        ) from error

    if baudrate <= 0 or timeout_seconds <= 0:
        raise RuntimeError(
            "Скорость и тайм-аут COM-порта должны быть больше нуля."
        )

    return MeterConnectionSettings(
        port=port,
        baudrate=baudrate,
        timeout_seconds=timeout_seconds,
    )


@dataclass(frozen=True, slots=True)
class MeterConnectionSettings:
    """Настройки соединения с оптической головкой."""

    port: str
    baudrate: int
    timeout_seconds: float


def load_meter_connection_settings() -> MeterConnectionSettings:
    """Читает локальные настройки COM-порта из файла .env."""
    load_dotenv()

    port = os.getenv("HLS_METER_PORT", "").strip()
    baudrate_text = os.getenv("HLS_METER_BAUDRATE", "").strip()
    timeout_text = os.getenv(
        "HLS_METER_TIMEOUT_SECONDS",
        "",
    ).strip()

    if not port:
        raise RuntimeError("Не задан HLS_METER_PORT.")

    try:
        baudrate = int(baudrate_text)
        timeout_seconds = float(timeout_text)
    except ValueError as error:
        raise RuntimeError(
            "Некорректные настройки скорости или тайм-аута COM-порта."
        ) from error

    if baudrate <= 0 or timeout_seconds <= 0:
        raise RuntimeError(
            "Скорость и тайм-аут COM-порта должны быть больше нуля."
        )

    return MeterConnectionSettings(
        port=port,
        baudrate=baudrate,
        timeout_seconds=timeout_seconds,
    )