"""Чтение журнала операций из SQL Server."""

from uuid import UUID

from hls_key_control.domain.models import Operation, OperationType
from hls_key_control.infrastructure.sql_server import open_connection


class SqlOperationJournal:
    """Возвращает последние операции из постоянного журнала SQL Server."""

    def recent(self, limit: int = 10) -> list[Operation]:
        """Возвращает последние операции: сначала самые новые."""
        if limit <= 0:
            raise ValueError("Лимит операций должен быть больше нуля.")

        connection = open_connection()

        try:
            cursor = connection.cursor()
            rows = cursor.execute(
                """
                SELECT TOP (?)
                    o.operation_id,
                    m.serial_number,
                    o.operation_type,
                    o.description,
                    o.created_at
                FROM dbo.Operations AS o
                INNER JOIN dbo.Meters AS m
                    ON o.meter_id = m.meter_id
                ORDER BY o.created_at DESC;
                """,
                limit,
            ).fetchall()

            return [
                Operation(
                    id=UUID(str(row.operation_id)),
                    meter_serial_number=str(row.serial_number),
                    operation_type=OperationType(str(row.operation_type)),
                    description=str(row.description),
                    created_at=row.created_at,
                )
                for row in rows
            ]
        finally:
            connection.close()