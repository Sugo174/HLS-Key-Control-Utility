"""Безопасное чтение DLMS invocation counter перед HLS/GMAC-сеансом."""

from gurux_dlms import GXReplyData
from gurux_dlms.enums import (
    Authentication,
    DataType,
    InterfaceType,
    Security,
)
from gurux_dlms.objects import GXDLMSData
from gurux_dlms.secure import GXDLMSSecureClient

from hls_key_control.config import load_dlms_settings
from hls_key_control.services.dlms_serial_reader import (
    _open_mode_e_connection,
    _read_data_block,
    _read_packet,
)


def read_invocation_counter_from_connection(connection) -> int:
    """Читает counter через уже открытый оптический канал."""
    settings = load_dlms_settings()

    client = GXDLMSSecureClient(
        useLogicalNameReferencing=True,
        clientAddress=16,
        serverAddress=settings.server_address,
        forAuthentication=Authentication.NONE,
        password=None,
        interfaceType=InterfaceType.HDLC_WITH_MODE_E,
    )
    client.ciphering.security = Security.NONE

    try:
        reply = GXReplyData()

        _read_packet(connection, client, client.snrmRequest(), reply)
        client.parseUAResponse(reply.data)

        reply.clear()
        _read_data_block(connection, client, client.aarqRequest(), reply)
        client.parseAareResponse(reply.data)

        counter_object = GXDLMSData(settings.invocation_counter_obis)
        request = client.read(counter_object, 2)[0]

        reply.clear()
        _read_data_block(connection, client, request, reply)

        if counter_object.getDataType(2) == DataType.NONE:
            counter_object.setDataType(2, reply.valueType)

        value = client.updateValue(counter_object, 2, reply.value)
        return int(value)
    finally:
        disconnect = client.disconnectRequest()

        if disconnect:
            connection.write(bytes(disconnect))
            connection.flush()


def read_invocation_counter(
    port: str | None = None,
) -> int:
    """Читает counter, самостоятельно открывая оптический канал."""
    connection = _open_mode_e_connection(port)

    try:
        return read_invocation_counter_from_connection(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    counter = read_invocation_counter()
    print(f"Счётчик вызовов ПУ прочитан: {counter}")