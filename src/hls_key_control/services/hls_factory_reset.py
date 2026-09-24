"""Физический сброс ПУ на заводской HLS."""

import time

from hls_key_control.config import (
    load_factory_hls_password_settings,
)
from hls_key_control.infrastructure.hls_key_repository import (
    SqlHlsKeyRepository,
)
from hls_key_control.infrastructure.meter_repository import (
    SqlMeterRepository,
)
from hls_key_control.services.dlms_hls_login_probe import (
    read_serial_number_with_hls_password,
)
from hls_key_control.services.dlms_hls_password_writer import (
    write_hls_password,
)


def reset_meter_to_factory_hls(
    port: str | None = None,
) -> str:
    """Возвращает ПУ к заводскому HLS и подтверждает это входом."""
    factory_password = (
        load_factory_hls_password_settings().password
    )

    write_hls_password(
        factory_password.decode("ascii"),
        port,
    )

    time.sleep(1.0)

    serial_number = read_serial_number_with_hls_password(
        port=port,
        password=factory_password,
    )

    meter = SqlMeterRepository().find_by_serial(
        serial_number,
    )

    if meter is None:
        raise RuntimeError("ПУ отсутствует в реестре SQL Server.")

    SqlHlsKeyRepository().confirm_physical_reset(meter)

    return serial_number


if __name__ == "__main__":
    serial_number = reset_meter_to_factory_hls()

    print(
        "Заводской HLS записан и подтверждён. "
        f"ПУ: {serial_number}"
    )