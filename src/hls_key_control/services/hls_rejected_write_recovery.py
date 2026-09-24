"""Восстановление реестра после подтверждённого отказа записи HLS."""

from hls_key_control.infrastructure.hls_key_repository import (
    SqlHlsKeyRepository,
)
from hls_key_control.infrastructure.meter_repository import (
    SqlMeterRepository,
)
from hls_key_control.services.dlms_hls_login_probe import (
    read_serial_number_with_hls_password,
)


def recover_rejected_hls_write() -> str:
    """Подтверждает прежний HLS и восстанавливает статус реестра."""
    serial_number = read_serial_number_with_hls_password()

    meter = SqlMeterRepository().find_by_serial(serial_number)

    if meter is None:
        raise RuntimeError("ПУ отсутствует в реестре.")

    SqlHlsKeyRepository().confirm_hls_write_rejected(meter)

    return meter.serial_number


if __name__ == "__main__":
    serial_number = recover_rejected_hls_write()
    print(
        "Отказ записи HLS подтверждён. "
        f"ПУ {serial_number} возвращён в состояние reset."
    )