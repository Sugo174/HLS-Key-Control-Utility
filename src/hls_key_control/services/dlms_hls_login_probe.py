"""Проверка HLS-входа в ПУ без изменения его настроек."""

import serial

from gurux_dlms import GXReplyData
from gurux_dlms.enums import (
    Authentication,
    DataType,
    InterfaceType,
    Security,
)
from gurux_dlms.objects import GXDLMSData
from gurux_dlms.secure import GXDLMSSecureClient

from hls_key_control.config import (
    load_dlms_settings,
    load_hls_password_settings,
)
from hls_key_control.services.dlms_serial_reader import (
    _as_serial_number,
    _open_mode_e_connection,
    _read_data_block,
    _read_packet,
)


def create_hls_password_client(
    password: bytes | None = None,
) -> GXDLMSSecureClient:
    """Создаёт клиента для обычной HLS-аутентификации по паролю."""
    dlms_settings = load_dlms_settings()

    if password is None:
        password = load_hls_password_settings().password

    client = GXDLMSSecureClient(
        useLogicalNameReferencing=True,
        clientAddress=dlms_settings.client_address,
        serverAddress=dlms_settings.server_address,
        forAuthentication=Authentication.HIGH,
        password=password,
        interfaceType=InterfaceType.HDLC_WITH_MODE_E,
    )

    client.ciphering.security = Security.NONE

    return client


def read_serial_number_with_hls_password(
    port: str | None = None,
    password: bytes | None = None,
) -> str:
    """Проверяет HLS-вход и читает серийный номер ПУ."""
    dlms_settings = load_dlms_settings()
    client = create_hls_password_client(password)
    connection = _open_mode_e_connection(port)

    try:
        reply = GXReplyData()

        _read_packet(connection, client, client.snrmRequest(), reply)
        client.parseUAResponse(reply.data)

        reply.clear()
        _read_data_block(connection, client, client.aarqRequest(), reply)
        client.parseAareResponse(reply.data)

        reply.clear()
        for request in client.getApplicationAssociationRequest():
            _read_packet(connection, client, request, reply)

        client.parseApplicationAssociationResponse(reply.data)

        serial_object = GXDLMSData(
            dlms_settings.serial_number_obis,
        )
        request = client.read(serial_object, 2)[0]

        reply.clear()
        _read_data_block(connection, client, request, reply)

        if serial_object.getDataType(2) == DataType.NONE:
            serial_object.setDataType(2, reply.valueType)

        value = client.updateValue(
            serial_object,
            2,
            reply.value,
        )
        serial_number = _as_serial_number(value)

        if not serial_number:
            raise RuntimeError("ПУ вернул пустой серийный номер.")

        return serial_number
    finally:
        try:
            disconnect = client.disconnectRequest()

            if disconnect:
                connection.write(bytes(disconnect))
                connection.flush()
        finally:
            connection.close()


if __name__ == "__main__":
    serial_number = read_serial_number_with_hls_password()
    print(f"HLS-вход подтверждён. Серийный номер ПУ: {serial_number}")