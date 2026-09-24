"""Создание настроенного защищённого DLMS-клиента."""

from gurux_dlms.enums import (
    Authentication,
    InterfaceType,
    Security,
)
from gurux_dlms.secure import GXDLMSSecureClient

from hls_key_control.config import load_dlms_settings


def create_dlms_client() -> GXDLMSSecureClient:
    """Создаёт клиента для оптического IEC Mode E / HDLC-сеанса."""
    settings = load_dlms_settings()

    client = GXDLMSSecureClient(
        useLogicalNameReferencing=True,
        clientAddress=settings.client_address,
        serverAddress=settings.server_address,
        forAuthentication=Authentication.HIGH_GMAC,
        password=None,
        interfaceType=InterfaceType.HDLC_WITH_MODE_E,
    )

    client.ciphering.systemTitle = settings.system_title
    client.ciphering.authenticationKey = settings.authentication_key
    client.ciphering.blockCipherKey = settings.block_cipher_key
    client.ciphering.security = Security.NONE

    return client