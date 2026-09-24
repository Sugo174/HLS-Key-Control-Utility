"""Смена HLS-пароля ПУ через действующий GMAC-сеанс."""

import time

from gurux_dlms import GXReplyData
from gurux_dlms.enums import DataType
from gurux_dlms.objects import GXDLMSAssociationLogicalName

from hls_key_control.services.dlms_client_factory import (
    create_dlms_client,
)
from hls_key_control.services.dlms_invocation_counter_reader import (
    read_invocation_counter,
)
from hls_key_control.services.dlms_serial_reader import (
    _open_mode_e_connection,
    _read_data_block,
    _read_packet,
)


class HlsWriteCommandUncertainError(RuntimeError):
    """Команда смены HLS могла быть принята ПУ, но ответ не получен."""


def _validate_new_hls_password(new_password: str) -> bytes:
    """Проверяет формат нового HLS, не выводя его значение."""
    if (
        len(new_password) != 16
        or not new_password.isascii()
        or not new_password.isdigit()
    ):
        raise ValueError(
            "Новый HLS должен содержать ровно 16 цифр."
        )

    return new_password.encode("ascii")


def write_hls_password(
    new_password: str,
    port: str | None = None,
) -> None:
    """Записывает HLS, повторяя только безопасные подготовительные шаги."""
    password_bytes = _validate_new_hls_password(new_password)
    last_error: Exception | None = None

    for attempt in range(1, 4):
        connection = None
        client = None
        command_started = False

        try:
            invocation_counter = read_invocation_counter(port)
            time.sleep(1.0)

            client = create_dlms_client()
            client.ciphering.invocationCounter = (
                invocation_counter + 1
            )

            connection = _open_mode_e_connection(port)
            reply = GXReplyData()

            _read_packet(
                connection,
                client,
                client.snrmRequest(),
                reply,
            )
            client.parseUAResponse(reply.data)

            reply.clear()
            _read_data_block(
                connection,
                client,
                client.aarqRequest(),
                reply,
            )
            client.parseAareResponse(reply.data)

            reply.clear()
            for request in client.getApplicationAssociationRequest():
                _read_packet(
                    connection,
                    client,
                    request,
                    reply,
                )

            client.parseApplicationAssociationResponse(reply.data)
            time.sleep(1.0)

            association = GXDLMSAssociationLogicalName()
            association.logicalName = "0.0.40.0.3.255"

            request = client.method(
                association,
                2,
                password_bytes,
                DataType.OCTET_STRING,
            )

            # После этой точки повторять запись нельзя:
            # ПУ мог применить пароль, даже если ответ потерян.
            command_started = True
            reply.clear()
            _read_data_block(
                connection,
                client,
                request,
                reply,
            )
            return

        except Exception as error:
            if command_started:
                raise HlsWriteCommandUncertainError(
                    "Команда изменения HLS отправлена, но ПУ не "
                    "подтвердил результат. Повторная запись запрещена; "
                    "нужна проверка входом."
                ) from error

            last_error = error

            if attempt == 3:
                raise RuntimeError(
                    "Не удалось подготовить защищённый сеанс с ПУ "
                    "за 3 попытки."
                ) from error

            time.sleep(2.0)

        finally:
            if connection is not None:
                try:
                    if client is not None:
                        disconnect = client.disconnectRequest()

                        if disconnect:
                            connection.write(bytes(disconnect))
                            connection.flush()
                finally:
                    connection.close()

    raise RuntimeError(
        "Не удалось подготовить защищённый сеанс с ПУ."
    ) from last_error