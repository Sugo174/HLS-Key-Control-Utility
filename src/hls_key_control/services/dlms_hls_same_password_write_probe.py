"""Контролируемая запись текущего HLS для проверки команды приложения."""

from hls_key_control.config import load_hls_password_settings
from hls_key_control.services.dlms_hls_login_probe import (
    read_serial_number_with_hls_password,
)
from hls_key_control.services.dlms_hls_password_writer import (
    write_hls_password,
)


def verify_hls_write_command() -> str:
    """Записывает текущий HLS и проверяет последующий вход."""
    password = load_hls_password_settings().password

    write_hls_password(password.decode("ascii"))

    return read_serial_number_with_hls_password(
        password=password,
    )


if __name__ == "__main__":
    serial_number = verify_hls_write_command()
    print(
        "Команда записи HLS принята ПУ. "
        f"Вход подтверждён, серийный номер: {serial_number}"
    )