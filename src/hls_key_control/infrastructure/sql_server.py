"""Подключение к SQL Server для HLS Key Control Utility."""

import pyodbc

from hls_key_control.config import load_database_settings


def open_connection() -> pyodbc.Connection:
    """Открывает подключение к служебной базе SQL Server."""
    settings = load_database_settings()
    return pyodbc.connect(
        settings.connection_string(),
        autocommit=False,
    )


def verify_connection() -> tuple[str, str]:
    """Проверяет, что подключение открыто для нужной базы и учётной записи."""
    connection = open_connection()

    try:
        cursor = connection.cursor()
        row = cursor.execute(
            "SELECT DB_NAME() AS database_name, "
            "SUSER_SNAME() AS connected_login;"
        ).fetchone()

        return str(row.database_name), str(row.connected_login)
    finally:
        connection.close()


if __name__ == "__main__":
    database_name, login_name = verify_connection()
    print(
        "Подключение к SQL Server выполнено. "
        f"База: {database_name}; учётная запись: {login_name}."
    )