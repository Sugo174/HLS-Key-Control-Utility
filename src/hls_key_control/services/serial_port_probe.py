"""Безопасная проверка доступа к оптической головке."""

import serial

from hls_key_control.config import load_meter_connection_settings


def verify_meter_port() -> str:
    """Открывает и сразу закрывает COM-порт без обмена со счётчиком."""
    settings = load_meter_connection_settings()

    try:
        connection = serial.Serial(
            port=settings.port,
            baudrate=settings.baudrate,
            timeout=settings.timeout_seconds,
            write_timeout=settings.timeout_seconds,
        )
    except serial.SerialException as error:
        raise RuntimeError(
            f"Не удалось открыть порт {settings.port}."
        ) from error

    try:
        return connection.name
    finally:
        connection.close()


if __name__ == "__main__":
    port_name = verify_meter_port()
    print(f"Оптическая головка доступна: {port_name}.")