"""Повторная проверка HLS-входа без изменения ПУ."""

import time

from collections.abc import Callable

from hls_key_control.services.dlms_hls_login_probe import (
    read_serial_number_with_hls_password,
)

ProgressReporter = Callable[[str], None]


def verify_hls_login_with_retries(
    password: bytes,
    port: str | None = None,
    progress: ProgressReporter | None = None,
) -> str:
    """Проверяет HLS-вход до трёх раз без повторной записи."""
    last_error: Exception | None = None

    for attempt in range(1, 4):
        if attempt == 1:
            _report_progress(
                progress,
                "Ожидание применения HLS в ПУ...",
            )
            time.sleep(3.0)
        else:
            _report_progress(
                progress,
                f"Повторная проверка HLS: попытка {attempt} из 3...",
            )
            time.sleep(2.0)

        try:
            return read_serial_number_with_hls_password(
                port=port,
                password=password,
            )
        except Exception as error:
            last_error = error

    raise RuntimeError(
        "ПУ не подтвердил HLS-вход за 3 попытки."
    ) from last_error


def _report_progress(
    progress: ProgressReporter | None,
    message: str,
) -> None:
    if progress is not None:
        progress(message)