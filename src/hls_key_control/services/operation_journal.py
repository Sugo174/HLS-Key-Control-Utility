"""Временный журнал производственных операций."""

from hls_key_control.domain.models import Operation, OperationType


class OperationJournal:
    """Хранит журнал действий в памяти до подключения SQL Server."""

    def __init__(self) -> None:
        self._operations: list[Operation] = []

    def record(
        self,
        meter_serial_number: str,
        operation_type: OperationType,
        description: str,
    ) -> Operation:
        """Добавляет новую неизменяемую запись в журнал."""

        operation = Operation(
            meter_serial_number=meter_serial_number,
            operation_type=operation_type,
            description=description,
        )

        self._operations.append(operation)

        return operation

    def recent(self, limit: int = 10) -> list[Operation]:
        """Возвращает последние операции: сначала самые новые."""

        return list(reversed(self._operations[-limit:]))