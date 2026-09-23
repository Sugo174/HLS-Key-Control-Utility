"""Загрузка локальных настроек подключения к SQL Server."""

import os
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