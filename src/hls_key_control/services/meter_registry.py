"""Временный реестр счётчиков для разработки бизнес-логики."""

from hls_key_control.domain.models import Meter


class MeterRegistry:
    """Хранит карточки ПУ в памяти до подключения SQL Server."""

    def __init__(self) -> None:
        self._meters_by_serial: dict[str, Meter] = {}

    def get_or_create(self, serial_number: str) -> tuple[Meter, bool]:
        """Возвращает ПУ и признак того, что он был создан впервые."""

        normalized_serial = serial_number.strip().upper()

        if not normalized_serial:
            raise ValueError("Серийный номер не может быть пустым.")

        meter = self._meters_by_serial.get(normalized_serial)

        if meter is not None:
            return meter, False

        meter = Meter(serial_number=normalized_serial)
        self._meters_by_serial[normalized_serial] = meter

        return meter, True