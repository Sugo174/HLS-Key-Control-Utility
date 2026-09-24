"""Физический сброс ПУ на заводской HLS."""

from hls_key_control.config import (
    load_factory_hls_password_settings,
)
from hls_key_control.infrastructure.hls_key_repository import (
    SqlHlsKeyRepository,
)
from hls_key_control.infrastructure.meter_repository import (
    SqlMeterRepository,
)
from hls_key_control.services.dlms_hls_password_writer import (
    HlsWriteCommandUncertainError,
    write_hls_password,
)
from hls_key_control.services.hls_login_verification import (
    verify_hls_login_with_retries,
)


def reset_meter_to_factory_hls(
    port: str | None = None,
) -> str:
    """Возвращает ПУ к заводскому HLS и подтверждает это входом."""
    factory_password = (
        load_factory_hls_password_settings().password
    )
    command_uncertain = False

    try:
        write_hls_password(
            factory_password.decode("ascii"),
            port,
        )
    except HlsWriteCommandUncertainError:
        command_uncertain = True

    try:
        serial_number = verify_hls_login_with_retries(
            password=factory_password,
            port=port,
        )
    except Exception as error:
        if command_uncertain:
            raise RuntimeError(
                "Команда сброса могла быть принята ПУ, но заводской "
                "HLS не удалось подтвердить. Не повторяйте сброс; "
                "сначала проверьте вход действующим HLS."
            ) from error

        raise

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