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


@dataclass(frozen=True, slots=True)
class DlmsSettings:
    """Локальные параметры защищённого DLMS-сеанса."""

    client_address: int
    server_address: int
    authentication: str
    security: str
    invocation_counter_obis: str
    serial_number_obis: str
    system_title: bytes
    authentication_key: bytes
    block_cipher_key: bytes


def load_dlms_settings() -> DlmsSettings:
    """Читает и проверяет DLMS-настройки из файла .env."""
    load_dotenv()

    required_names = (
        "HLS_DLMS_CLIENT_ADDRESS",
        "HLS_DLMS_SERVER_ADDRESS",
        "HLS_DLMS_AUTHENTICATION",
        "HLS_DLMS_SECURITY",
        "HLS_DLMS_INVOCATION_COUNTER_OBIS",
        "HLS_DLMS_SERIAL_NUMBER_OBIS",
        "HLS_DLMS_SYSTEM_TITLE_HEX",
        "HLS_DLMS_AUTHENTICATION_KEY_HEX",
        "HLS_DLMS_BLOCK_CIPHER_KEY_HEX",
    )

    values = {name: os.getenv(name, "").strip() for name in required_names}
    missing_names = [name for name, value in values.items() if not value]

    if missing_names:
        raise RuntimeError(
            "Не заполнены настройки DLMS: "
            + ", ".join(missing_names)
            + "."
        )

    try:
        client_address = int(values["HLS_DLMS_CLIENT_ADDRESS"])
        server_address = int(values["HLS_DLMS_SERVER_ADDRESS"])
    except ValueError as error:
        raise RuntimeError(
            "DLMS-адреса должны быть целыми числами."
        ) from error

    system_title = _decode_dlms_hex(
        values["HLS_DLMS_SYSTEM_TITLE_HEX"],
        "HLS_DLMS_SYSTEM_TITLE_HEX",
    )
    authentication_key = _decode_dlms_hex(
        values["HLS_DLMS_AUTHENTICATION_KEY_HEX"],
        "HLS_DLMS_AUTHENTICATION_KEY_HEX",
    )
    block_cipher_key = _decode_dlms_hex(
        values["HLS_DLMS_BLOCK_CIPHER_KEY_HEX"],
        "HLS_DLMS_BLOCK_CIPHER_KEY_HEX",
    )

    if len(system_title) != 8:
        raise RuntimeError(
            "HLS_DLMS_SYSTEM_TITLE_HEX должен содержать 8 байт."
        )

    if len(authentication_key) != 16:
        raise RuntimeError(
            "HLS_DLMS_AUTHENTICATION_KEY_HEX должен содержать 16 байт."
        )

    if len(block_cipher_key) != 16:
        raise RuntimeError(
            "HLS_DLMS_BLOCK_CIPHER_KEY_HEX должен содержать 16 байт."
        )

    if values["HLS_DLMS_AUTHENTICATION"] != "HIGH_GMAC":
        raise RuntimeError(
            "Поддерживается только HLS_DLMS_AUTHENTICATION=HIGH_GMAC."
        )

    security = values["HLS_DLMS_SECURITY"].upper()

    if security not in {
        "NONE",
        "AUTHENTICATION",
        "AUTHENTICATION_ENCRYPTION",
    }:
        raise RuntimeError(
            "HLS_DLMS_SECURITY содержит неподдерживаемый режим."
        )

    return DlmsSettings(
        client_address=client_address,
        server_address=server_address,
        authentication=values["HLS_DLMS_AUTHENTICATION"],
        security=security,
        invocation_counter_obis=values[
            "HLS_DLMS_INVOCATION_COUNTER_OBIS"
        ],
        serial_number_obis=values[
            "HLS_DLMS_SERIAL_NUMBER_OBIS"
        ],
        system_title=system_title,
        authentication_key=authentication_key,
        block_cipher_key=block_cipher_key,
    )


def _decode_dlms_hex(value: str, setting_name: str) -> bytes:
    """Преобразует шестнадцатеричную настройку в байты."""
    try:
        return bytes.fromhex(value)
    except ValueError as error:
        raise RuntimeError(
            f"{setting_name} содержит некорректное HEX-значение."
        ) from error