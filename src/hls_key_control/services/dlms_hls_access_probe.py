"""Проверка прав GMAC-сеанса на смену HLS без изменения ПУ."""

import time

from gurux_dlms import GXReplyData
from gurux_dlms.enums import ObjectType
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


def read_hls_change_access(
    port: str | None = None,
) -> str:
    """Возвращает право на действие смены HLS."""
    invocation_counter = read_invocation_counter(port)
    time.sleep(1.0)

    client = create_dlms_client()
    client.ciphering.invocationCounter = invocation_counter + 1
    connection = _open_mode_e_connection(port)

    try:
        reply = GXReplyData()

        _read_packet(connection, client, client.snrmRequest(), reply)
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
            _read_packet(connection, client, request, reply)

        client.parseApplicationAssociationResponse(reply.data)

        association = GXDLMSAssociationLogicalName()
        request = client.read(association, 2)[0]

        reply.clear()
        _read_data_block(connection, client, request, reply)
        client.updateValue(association, 2, reply.value)

        target = association.objectList.findByLN(
            ObjectType.ASSOCIATION_LOGICAL_NAME,
            association.logicalName,
        )

        if target is None:
            raise RuntimeError(
                "ПУ не вернул объект Association Logical Name."
            )

        return target.getMethodAccess(2).name
    finally:
        try:
            disconnect = client.disconnectRequest()

            if disconnect:
                connection.write(bytes(disconnect))
                connection.flush()
        finally:
            connection.close()


if __name__ == "__main__":
    access = read_hls_change_access()
    print(f"Право GMAC на смену HLS: {access}")