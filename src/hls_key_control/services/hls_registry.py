"""Временный реестр созданных HLS-паролей."""

import secrets
from uuid import uuid4

from hls_key_control.domain.models import (
    HlsKey,
    HlsKeyStatus,
    Meter,
    MeterStatus,
)
from hls_key_control.services.hls_generator import (
    create_hls_fingerprint,
    generate_hls_password,
)


class HlsRegistry:
    """Создаёт уникальные HLS-пароли и не допускает их повторного использования."""

    def __init__(self) -> None:
        self._fingerprint_secret = secrets.token_bytes(32)
        self._used_fingerprints: set[str] = set()
        self._keys_by_id: dict[str, HlsKey] = {}

    def reserve_new_key(self, meter: Meter) -> tuple[HlsKey, str]:
        """Создаёт новый HLS и резервирует его для конкретного ПУ."""

        if meter.status not in {
            MeterStatus.NEW,
            MeterStatus.RESET,
        }:
            raise ValueError(
                "Для данного ПУ нельзя создать новый HLS в текущем статусе."
            )

        while True:
            hls_password = generate_hls_password()
            fingerprint = create_hls_fingerprint(
                hls_password,
                self._fingerprint_secret,
            )

            if fingerprint not in self._used_fingerprints:
                break

        key = HlsKey(
            id=uuid4(),
            meter_serial_number=meter.serial_number,
            status=HlsKeyStatus.RESERVED,
            fingerprint=fingerprint,
        )

        self._used_fingerprints.add(fingerprint)
        self._keys_by_id[str(key.id)] = key

        meter.current_hls_key_id = key.id
        meter.status = MeterStatus.OPERATION_UNCONFIRMED

        return key, hls_password

    def confirm_reset_for_test(self, meter: Meter) -> None:
        """Имитирует подтверждённый сервисный сброс HLS в тестовом режиме."""

        if meter.current_hls_key_id is None:
            raise ValueError("У данного ПУ нет HLS, который можно отозвать.")

        key_id = str(meter.current_hls_key_id)
        key = self._keys_by_id.get(key_id)

        if key is None:
            raise ValueError("Не найдена запись HLS для текущего ПУ.")

        key.status = HlsKeyStatus.RETIRED

        meter.current_hls_key_id = None
        meter.status = MeterStatus.RESET