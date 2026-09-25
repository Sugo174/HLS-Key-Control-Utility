"""Чтение серийного номера ПУ через IEC Mode E и DLMS/HDLC."""

import time
import serial
import os

from collections.abc import Callable

from gurux_dlms import GXByteBuffer, GXReplyData
from gurux_dlms.enums import DataType
from gurux_dlms.objects import GXDLMSData

from hls_key_control.config import (
    load_dlms_settings,
    load_meter_connection_settings,
)
from hls_key_control.services.dlms_client_factory import (
    create_dlms_client,
)

ProgressReporter = Callable[[str], None]

def _report_progress(
    progress: ProgressReporter | None,
    message: str,
) -> None:
    """Передаёт безопасный статус операции в интерфейс."""
    if progress is not None:
        progress(message)


def _read_hdlc_frame(connection: serial.Serial) -> bytes:
    """Читает HDLC-кадр по длине из его заголовка."""
    while True:
        value = connection.read(1)

        if not value:
            raise RuntimeError("ПУ не ответил на DLMS-запрос.")

        if value == b"\x7E":
            break

    header = connection.read(2)

    if len(header) != 2:
        raise RuntimeError("ПУ вернул неполный заголовок HDLC-кадра.")

    frame_length = (
        ((header[0] & 0x07) << 8)
        | header[1]
    )

    if frame_length < 3:
        raise RuntimeError("ПУ вернул некорректную длину HDLC-кадра.")

    remainder = connection.read(frame_length - 1)

    if len(remainder) != frame_length - 1:
        raise RuntimeError("ПУ вернул неполный HDLC-кадр.")

    frame = b"\x7E" + header + remainder

    if frame[-1:] != b"\x7E":
        raise RuntimeError("ПУ вернул повреждённый HDLC-кадр.")

    return frame


def _read_packet(
    connection: serial.Serial,
    client,
    request,
    reply: GXReplyData,
) -> None:
    """Отправляет один или несколько DLMS-запросов и получает ответы."""
    if not request:
        return

    if isinstance(request, list):
        for packet in request:
            reply.clear()
            _read_packet(connection, client, packet, reply)
        return
   
    connection.write(bytes(request))
    connection.flush()

    received = GXByteBuffer()

    while not client.getData(received, reply):
        frame = _read_hdlc_frame(connection)
        received.set(frame)

    if reply.error != 0:
        raise RuntimeError(f"ПУ отклонил DLMS-запрос, код: {reply.error}.")


def _read_data_block(
    connection: serial.Serial,
    client,
    request,
    reply: GXReplyData,
) -> None:
    """Получает ответ DLMS, включая возможные блоки продолжения."""
    if isinstance(request, list):
        for packet in request:
            reply.clear()
            _read_data_block(connection, client, packet, reply)
        return

    _read_packet(connection, client, request, reply)

    while reply.isMoreData():
        continuation = client.receiverReady(reply)
        _read_packet(connection, client, continuation, reply)


def _open_mode_e_connection(
    port: str | None = None,
) -> serial.Serial:
    """Открывает COM-порт и переводит ПУ в HDLC-режим."""
    settings = load_meter_connection_settings()

    connection = serial.Serial(
        port=port or settings.port,
        baudrate=300,
        bytesize=serial.SEVENBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        timeout=settings.timeout_seconds,
        write_timeout=settings.timeout_seconds,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )

    try:
        time.sleep(0.3)
        connection.reset_input_buffer()
        connection.write(b"/?!\r\n")
        connection.flush()

        identification = connection.read_until(b"\n")

        if not identification.startswith(b"/"):
            raise RuntimeError("ПУ не вернул строку IEC-идентификации.")

        if identification[4:5] != b"5":
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
        return connection
    except Exception:
        connection.close()
        raise


def _disconnect_dlms_session(
    connection: serial.Serial,
    client,
) -> None:
    """Корректно завершает HDLC-сеанс и ждёт ответ ПУ."""
    original_timeout = connection.timeout

    try:
        disconnect = client.disconnectRequest()

        if not disconnect:
            return

        connection.timeout = max(
            float(original_timeout or 0.0),
            3.0,
        )

        reply = GXReplyData()
        _read_packet(connection, client, disconnect, reply)

        try:
            client.parseUAResponse(reply.data)
        except Exception:
            pass
    except Exception:
        # Ошибка при завершении не должна скрыть результат
        # основной операции.
        pass
    finally:
        connection.timeout = original_timeout

        try:
            connection.reset_input_buffer()
        except serial.SerialException:
            pass


def _close_dlms_connection(
    connection: serial.Serial,
    client,
) -> None:
    """Завершает DLMS-сеанс и закрывает COM-порт."""
    try:
        _disconnect_dlms_session(connection, client)
    finally:
        connection.close()


def _as_serial_number(value: object) -> str:
    """Приводит значение DLMS к строке серийного номера."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("ascii").strip()

    return str(value).strip()


def read_meter_serial_number(
    port: str | None = None,
    progress: ProgressReporter | None = None,
) -> str:
    """Устанавливает HLS/GMAC-сеанс и читает серийный номер ПУ."""
    from hls_key_control.services.dlms_invocation_counter_reader import (
        read_invocation_counter,
    )
    dlms_settings = load_dlms_settings()

    _report_progress(
        progress,
        "Проверка защищённого соединения...",
    )

    _report_progress(
        progress,
        "Проверка защищённого соединения...",
    )
    try:
        invocation_counter = read_invocation_counter(port)
    except Exception as error:
        raise RuntimeError(
            "Не удалось прочитать счётчик вызовов перед "
            f"GMAC-сеансом: {error}"
        ) from error

    time.sleep(1.0)

    _report_progress(
        progress,
        "Подключение к счётчику...",
    )
    client = create_dlms_client()
    client.ciphering.invocationCounter = invocation_counter + 1
    connection = _open_mode_e_connection(port)

    try:
        reply = GXReplyData()
        stage = "открытие HDLC-канала"
        _read_packet(connection, client, client.snrmRequest(), reply)
        client.parseUAResponse(reply.data)

        reply.clear()
        stage = "установление DLMS-ассоциации"
        _read_data_block(connection, client, client.aarqRequest(), reply)
        client.parseAareResponse(reply.data)

        stage = "GMAC-подтверждение"
        reply.clear()
        for request in client.getApplicationAssociationRequest():
            _read_packet(connection, client, request, reply)

        client.parseApplicationAssociationResponse(reply.data)

        _report_progress(
            progress,
            "Считывание серийного номера...",
        )
        stage = "чтение серийного номера"
        serial_object = GXDLMSData(dlms_settings.serial_number_obis)
        request = client.read(serial_object, 2)[0]

        reply.clear()
        _read_data_block(connection, client, request, reply)

        if serial_object.getDataType(2) == DataType.NONE:
            serial_object.setDataType(2, reply.valueType)

        value = client.updateValue(serial_object, 2, reply.value)
        serial_number = _as_serial_number(value)

        if not serial_number:
            raise RuntimeError("ПУ вернул пустой серийный номер.")

        return serial_number
    except Exception as error:
        raise RuntimeError(
            f"GMAC-сеанс, этап «{stage}»: {error}"
        ) from error 
    finally:
        _close_dlms_connection(connection, client)


if __name__ == "__main__":
    serial_number = read_meter_serial_number()
    print(f"Серийный номер ПУ: {serial_number}")