"""Сервисная замена HLS без включения ключа в тендерную выгрузку."""

from collections.abc import Callable

from hls_key_control.domain.models import Meter
from hls_key_control.infrastructure.hls_key_repository import (
    SqlHlsKeyRepository,
)
from hls_key_control.services.dlms_hls_password_writer import (
    HlsWriteCommandUncertainError,
    write_hls_password,
)
from hls_key_control.services.hls_login_verification import (
    verify_hls_login_with_retries,
)

ProgressReporter = Callable[[str], None]


def _report_progress(
    progress: ProgressReporter | None,
    message: str,
) -> None:
    """Передаёт в интерфейс безопасный статус операции."""
    if progress is not None:
        progress(message)


def write_service_hls(
    meter: Meter,
    port: str | None = None,
    progress: ProgressReporter | None = None,
) -> str:
    """Записывает сервисный HLS и подтверждает его новым входом."""
    repository = SqlHlsKeyRepository()
    _, new_password = repository.reserve_service_key(meter)

    try:
        _report_progress(
            progress,
            "Запись сервисного HLS в счётчик...",
        )
        write_hls_password(new_password, port)
    except HlsWriteCommandUncertainError:
        _report_progress(
            progress,
            "Ответ на запись не получен. "
            "Проверяем сервисный HLS...",
        )
    except Exception:
        repository.cancel_reserved_service_hls_before_write(
            meter
        )
        raise

    try:
        verified_serial_number = verify_hls_login_with_retries(
            password=new_password.encode("ascii"),
            port=port,
            progress=progress,
        )

        if (
            verified_serial_number.strip().upper()
            != meter.serial_number.strip().upper()
        ):
            raise RuntimeError(
                "Сервисный HLS подтвердил другой серийный "
                "номер счётчика."
            )
    except Exception:
        try:
            repository.mark_service_hls_write_unconfirmed(meter)
        except Exception:
            pass

        raise

    try:
        repository.confirm_service_hls_written(meter)
    except Exception as error:
        try:
            repository.mark_service_hls_write_unconfirmed(meter)
        except Exception:
            pass

        raise RuntimeError(
            "Сервисный HLS подтверждён на счётчике, "
            "но не подтверждён в реестре. "
            "Повторная запись заблокирована."
        ) from error

    _report_progress(
        progress,
        "Сервисный HLS записан и подтверждён.",
    )

    return verified_serial_number


def verify_unconfirmed_service_hls(
    meter: Meter,
    port: str | None = None,
    progress: ProgressReporter | None = None,
) -> str:
    """Проверяет сервисный HLS после неподтверждённой записи."""
    repository = SqlHlsKeyRepository()

    service_password = (
        repository.get_unconfirmed_service_hls_password(meter)
    )

    _report_progress(
        progress,
        "Проверка сервисного HLS без записи в счётчик...",
    )

    verified_serial_number = verify_hls_login_with_retries(
        password=service_password.encode("ascii"),
        port=port,
        progress=progress,
    )

    if (
        verified_serial_number.strip().upper()
        != meter.serial_number.strip().upper()
    ):
        raise RuntimeError(
            "Сервисный HLS подтвердил другой серийный "
            "номер счётчика."
        )

    try:
        repository.confirm_service_hls_written(meter)
    except Exception as error:
        raise RuntimeError(
            "Сервисный HLS подтверждён на счётчике, "
            "но не подтверждён в реестре. "
            "Повторная запись заблокирована."
        ) from error

    _report_progress(
        progress,
        "Сервисный HLS подтверждён.",
    )

    return verified_serial_number