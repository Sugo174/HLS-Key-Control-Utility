"""Безопасная проверка состояния HLS после сбоя сброса."""

from collections.abc import Callable
from dataclasses import dataclass

from hls_key_control.config import (
    load_factory_hls_password_settings,
)
from hls_key_control.domain.models import Meter
from hls_key_control.infrastructure.hls_key_repository import (
    SqlHlsKeyRepository,
)
from hls_key_control.services.hls_login_verification import (
    verify_hls_login_with_retries,
)

ProgressReporter = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class HlsResetRecoveryResult:
    """Результат проверки состояния HLS без записи в счётчик."""

    serial_number: str
    factory_hls_confirmed: bool


def verify_hls_state_after_reset_failure(
    meter: Meter,
    port: str | None = None,
    progress: ProgressReporter | None = None,
) -> HlsResetRecoveryResult:
    """Определяет, сохранился ли активный HLS или применился заводской."""
    repository = SqlHlsKeyRepository()
    factory_password = (
        load_factory_hls_password_settings().password
    )

    _report_progress(
        progress,
        "Проверка входа с заводским HLS...",
    )

    try:
        serial_number = verify_hls_login_with_retries(
            password=factory_password,
            port=port,
            progress=progress,
        )
    except Exception:
        serial_number = ""

    if serial_number:
        _ensure_expected_meter(
            meter,
            serial_number,
        )
        repository.confirm_physical_reset(
            meter,
            description=(
                "Проверка состояния HLS: заводской HLS "
                "подтверждён, предыдущий ключ отозван."
            ),
        )

        return HlsResetRecoveryResult(
            serial_number=serial_number,
            factory_hls_confirmed=True,
        )

    _report_progress(
        progress,
        "Заводской HLS не подтверждён. "
        "Проверка персонального HLS...",
    )

    active_password = (
        repository.get_active_hls_password_for_reset_recovery(
            meter
        )
    )

    try:
        serial_number = verify_hls_login_with_retries(
            password=active_password.encode("ascii"),
            port=port,
            progress=progress,
        )
    except Exception as error:
        raise RuntimeError(
            "Не удалось подтвердить ни заводской, "
            "ни персональный HLS. Повторные записи заблокированы."
        ) from error

    _ensure_expected_meter(
        meter,
        serial_number,
    )
    repository.confirm_active_hls_after_reset_check(meter)

    return HlsResetRecoveryResult(
        serial_number=serial_number,
        factory_hls_confirmed=False,
    )


def _ensure_expected_meter(
    meter: Meter,
    serial_number: str,
) -> None:
    """Не позволяет обновить запись другого подключённого счётчика."""
    if serial_number != meter.serial_number:
        raise RuntimeError(
            "Подключён другой счётчик. Состояние HLS в реестре "
            "не было изменено."
        )


def _report_progress(
    progress: ProgressReporter | None,
    message: str,
) -> None:
    if progress is not None:
        progress(message)