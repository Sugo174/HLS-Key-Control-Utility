"""Подключение к SQL Server для HLS Key Control Utility."""

import time

import pyodbc

from hls_key_control.config import load_database_settings


_RETRYABLE_SQL_STATES = {
    "08001",
    "08S01",
    "HYT00",
    "HYT01",
}


def _is_retryable_connection_error(error: pyodbc.Error) -> bool:
    """Определяет кратковременный сбой подключения к SQL Server."""
    return bool(
        error.args
        and str(error.args[0]) in _RETRYABLE_SQL_STATES
    )


def open_connection() -> pyodbc.Connection:
    """Открывает подключение к служебной базе SQL Server с повторами."""
    settings = load_database_settings()
    last_error: pyodbc.Error | None = None

    for attempt in range(1, 4):
        try:
            return pyodbc.connect(
                settings.connection_string(),
                autocommit=False,
            )
        except pyodbc.Error as error:
            last_error = error

            if (
                not _is_retryable_connection_error(error)
                or attempt == 3
            ):
                raise

            time.sleep(float(attempt))

    raise RuntimeError(
        "Не удалось открыть подключение к SQL Server."
    ) from last_error


def verify_connection() -> tuple[str, str]:
    """Проверяет подключение к нужной базе и учётной записи."""
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