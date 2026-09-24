"""Контролируемая персонализация ПУ новым HLS."""

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


def personalize_meter_hls(
    meter: Meter,
    port: str | None = None,
    progress: ProgressReporter | None = None,
) -> str:
    """Записывает резервный HLS и подтверждает его новым входом."""
    repository = SqlHlsKeyRepository()
    new_password = repository.get_reserved_hls_password(meter)

    try:
        _report_progress(
            progress,
            "Запись нового HLS в ПУ...",
        )
        write_hls_password(new_password, port)
    except HlsWriteCommandUncertainError:
        _report_progress(
            progress,
            "Ответ на запись не получен. Проверяем новый HLS...",
        )
    except Exception:
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
                "Новый HLS подтвердил другой серийный номер ПУ."
            )
    except Exception:
        try:
            repository.mark_hls_write_unconfirmed(meter)
        except Exception:
            pass

        raise

    try:
        repository.confirm_hls_written(meter)
    except Exception as error:
        try:
            repository.mark_hls_write_unconfirmed(meter)
        except Exception:
            pass

        raise RuntimeError(
            "HLS подтверждён на ПУ, но не подтверждён в реестре. "
            "Повторная запись заблокирована."
        ) from error

    _report_progress(
        progress,
        "Новый HLS записан и подтверждён.",
    )

    return verified_serial_number