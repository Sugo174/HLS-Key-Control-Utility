"""Безопасный запрос идентификации счётчика по IEC 62056-21."""

import time

import serial

from hls_key_control.config import load_meter_connection_settings


IDENTIFICATION_REQUEST = b"/?!\r\n"


class MeterIdentificationError(RuntimeError):
    """Ошибка чтения идентификационной строки счётчика."""


def request_meter_identification() -> str:
    """Отправляет стандартный запрос идентификации и читает ответ ПУ."""
    settings = load_meter_connection_settings()

    try:
        connection = serial.Serial(
            port=settings.port,
            baudrate=300,
            bytesize=serial.SEVENBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=settings.timeout_seconds,
            write_timeout=settings.timeout_seconds,
        )
    except serial.SerialException as error:
        raise MeterIdentificationError(
            f"Не удалось открыть порт {settings.port}."
        ) from error

    try:
        time.sleep(0.2)
        connection.reset_input_buffer()
        connection.write(IDENTIFICATION_REQUEST)
        connection.flush()

        response = connection.read_until(b"\n")
    except serial.SerialException as error:
        raise MeterIdentificationError(
            "Не удалось выполнить запрос идентификации ПУ."
        ) from error
    finally:
        connection.close()

    if not response:
        raise MeterIdentificationError(
            "ПУ не ответил на запрос идентификации."
        )

    response_text = response.decode("ascii", errors="replace").strip()

    if not response_text.startswith("/"):
        raise MeterIdentificationError(
            "Получен ответ, не похожий на строку идентификации IEC 62056-21."
        )

    return response_text


if __name__ == "__main__":
    identification = request_meter_identification()
    print(f"Ответ ПУ: {identification}")