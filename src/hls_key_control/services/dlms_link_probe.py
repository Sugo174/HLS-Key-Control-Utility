"""Проверка IEC Mode E и HDLC-канала счётчика."""

import time

import serial
from gurux_dlms import GXByteBuffer, GXReplyData

from hls_key_control.config import load_meter_connection_settings
from hls_key_control.services.dlms_client_factory import (
    create_dlms_client,
)


def _read_hdlc_frame(connection: serial.Serial) -> bytes:
    """Читает один HDLC-кадр от начального до конечного флага."""
    frame = bytearray()
    started = False

    while True:
        value = connection.read(1)

        if not value:
            raise RuntimeError("ПУ не ответил HDLC-кадром.")

        if value == b"\x7E":
            if started:
                frame.extend(value)
                return bytes(frame)

            started = True

        if started:
            frame.extend(value)


def verify_dlms_link() -> None:
    """Переходит в Mode E и проверяет, что ПУ отвечает на SNRM."""
    connection_settings = load_meter_connection_settings()
    client = create_dlms_client()

    connection = serial.Serial(
        port=connection_settings.port,
        baudrate=300,
        bytesize=serial.SEVENBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        timeout=connection_settings.timeout_seconds,
        write_timeout=connection_settings.timeout_seconds,
    )

    try:
        time.sleep(0.3)
        connection.reset_input_buffer()
        connection.write(b"/?!\r\n")
        connection.flush()

        identification = connection.read_until(b"\n")

        if not identification.startswith(b"/"):
            raise RuntimeError(
                "ПУ не вернул строку IEC-идентификации."
            )

        baud_id = identification[4:5]

        if baud_id != b"5":
            raise RuntimeError(
                "ПУ не подтвердил ожидаемую скорость 9600 бод."
            )

        connection.write(b"\x06" + b"252\r\n")
        connection.flush()
        time.sleep(0.3)

        connection.baudrate = 9600
        connection.bytesize = serial.EIGHTBITS
        connection.parity = serial.PARITY_NONE
        connection.stopbits = serial.STOPBITS_ONE
        connection.reset_input_buffer()
        time.sleep(1.0)

        snrm = client.snrmRequest()
        connection.write(bytes(snrm))
        connection.flush()

        reply = GXReplyData()
        response = _read_hdlc_frame(connection)

        if not client.getData(GXByteBuffer(response), reply):
            raise RuntimeError("ПУ вернул неполный HDLC-ответ.")

        client.parseUAResponse(reply.data)

        disconnect = client.disconnectRequest()

        if disconnect:
            connection.write(bytes(disconnect))
            connection.flush()
    finally:
        connection.close()


if __name__ == "__main__":
    verify_dlms_link()
    print("IEC Mode E и HDLC-канал подтверждены.")