"""Партии экспорта HLS с защитой от повторной выгрузки."""

from datetime import datetime
from uuid import UUID

import pyodbc

from hls_key_control.domain.models import (
    ExportBatch,
    ExportBatchItem,
    ExportBatchStatus,
    ExportCandidate,
    HlsKeyStatus,
    MeterStatus,
    OperationType,
)

from hls_key_control.infrastructure.sql_server import open_connection


class SqlExportBatchRepository:
    """Фиксирует неизменяемый состав Excel-партий в SQL Server."""

    def list_export_candidates(
        self,
    ) -> list[tuple[ExportCandidate, bytes]]:
        """Возвращает HLS, доступные для новой выгрузки.

        Метод только читает БД: не создаёт партию и не резервирует
        ключи для экспорта.
        """
        connection = open_connection()

        try:
            cursor = connection.cursor()
            rows = cursor.execute(
                """
                SELECT
                    m.meter_id,
                    m.serial_number,
                    h.hls_key_id,
                    COALESCE(
                        activated.created_at,
                        h.created_at
                    ) AS personalized_at,
                    h.encrypted_value
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.hls_key_id = m.current_hls_key_id
                OUTER APPLY (
                    SELECT TOP (1)
                        o.created_at
                    FROM dbo.Operations AS o
                    WHERE
                        o.meter_id = m.meter_id
                        AND o.operation_type = ?
                    ORDER BY o.created_at DESC
                ) AS activated
                WHERE
                    m.status = ?
                    AND h.status = ?
                    AND NOT EXISTS (
                        SELECT 1
                        FROM dbo.ExportBatchItems AS item
                        WHERE item.hls_key_id = h.hls_key_id
                    )
                ORDER BY m.serial_number;
                """,
                OperationType.HLS_ACTIVATED.value,
                MeterStatus.PERSONALIZED.value,
                HlsKeyStatus.ACTIVE.value,
            ).fetchall()

            result: list[tuple[ExportCandidate, bytes]] = []

            for row in rows:
                if row.encrypted_value is None:
                    raise RuntimeError(
                        "Для подготовленной выгрузки отсутствует "
                        "защищённое значение HLS."
                    )

                result.append(
                    (
                        ExportCandidate(
                            meter_id=UUID(str(row.meter_id)),
                            hls_key_id=UUID(str(row.hls_key_id)),
                            serial_number=str(row.serial_number),
                            personalized_at=row.personalized_at,
                        ),
                        bytes(row.encrypted_value),
                    )
                )

            return result
        finally:
            connection.close()

    def create_batch_for_candidates(
        self,
        created_by: str,
        customer_name: str,
        order_number: str,
        hls_key_ids: list[UUID],
    ) -> ExportBatch:
        """Создаёт партию из точного набора HLS из окна подготовки."""
        customer_name = customer_name.strip()
        order_number = order_number.strip()

        if not customer_name:
            raise ValueError("Укажите заказчика для экспортной партии.")

        if not order_number:
            raise ValueError("Укажите номер заказа для экспортной партии.")

        if len(customer_name) > 160:
            raise ValueError(
                "Название заказчика не должно быть длиннее 160 символов."
            )

        if len(order_number) > 100:
            raise ValueError(
                "Номер заказа не должен быть длиннее 100 символов."
            )

        requested_ids = [
            UUID(str(hls_key_id))
            for hls_key_id in hls_key_ids
        ]

        if not requested_ids:
            raise ValueError(
                "Нет подтверждённых HLS для новой выгрузки."
            )

        if len(set(requested_ids)) != len(requested_ids):
            raise ValueError(
                "В состав выгрузки попали повторяющиеся HLS."
            )

        placeholders = ", ".join(
            "?"
            for _ in requested_ids
        )

        placeholders = ", ".join("?" for _ in requested_ids)
        connection = open_connection()

        try:
            cursor = connection.cursor()
            prefix = datetime.now().astimezone().strftime(
                "HLS-%Y%m%d"
            )

            last_batch = cursor.execute(
                """
                SELECT TOP (1) batch_number
                FROM dbo.ExportBatches WITH (
                    UPDLOCK,
                    SERIALIZABLE
                )
                WHERE batch_number LIKE ?
                ORDER BY batch_number DESC;
                """,
                f"{prefix}-%",
            ).fetchone()

            sequence = 1

            if last_batch is not None:
                sequence = (
                    int(
                        str(last_batch.batch_number).rsplit(
                            "-",
                            1,
                        )[1]
                    )
                    + 1
                )

            batch_number = f"{prefix}-{sequence:03d}"

            rows = cursor.execute(
                f"""
                SELECT
                    m.meter_id,
                    m.serial_number,
                    h.hls_key_id,
                    COALESCE(
                        activated.created_at,
                        h.created_at
                    ) AS personalized_at
                FROM dbo.HlsKeys AS h WITH (
                    UPDLOCK,
                    SERIALIZABLE
                )
                INNER JOIN dbo.Meters AS m WITH (
                    UPDLOCK,
                    SERIALIZABLE
                )
                    ON m.current_hls_key_id = h.hls_key_id
                OUTER APPLY (
                    SELECT TOP (1)
                        o.created_at
                    FROM dbo.Operations AS o
                    WHERE
                        o.meter_id = m.meter_id
                        AND o.operation_type = ?
                    ORDER BY o.created_at DESC
                ) AS activated
                WHERE
                    h.hls_key_id IN ({placeholders})
                    AND m.status = ?
                    AND h.status = ?
                    AND NOT EXISTS (
                        SELECT 1
                        FROM dbo.ExportBatchItems AS item
                        WHERE item.hls_key_id = h.hls_key_id
                    )
                ORDER BY m.serial_number;
                """,
                OperationType.HLS_ACTIVATED.value,
                *(str(hls_key_id) for hls_key_id in requested_ids),
                MeterStatus.PERSONALIZED.value,
                HlsKeyStatus.ACTIVE.value,
            ).fetchall()

            actual_ids = {
                UUID(str(row.hls_key_id))
                for row in rows
            }

            if (
                len(rows) != len(requested_ids)
                or actual_ids != set(requested_ids)
            ):
                raise ValueError(
                    "Состав доступных HLS изменился. "
                    "Обновите подготовку выгрузки."
                )

            batch_row = cursor.execute(
                """
                INSERT INTO dbo.ExportBatches (
                    batch_number,
                    created_by,
                    customer_name,
                    order_number
                )
                OUTPUT
                    inserted.export_batch_id,
                    inserted.batch_number,
                    inserted.status,
                    inserted.created_at,
                    inserted.created_by,
                    inserted.row_count,
                    inserted.customer_name,
                    inserted.order_number
                VALUES (?, ?, ?, ?);
                """,
                batch_number,
                created_by,
                customer_name,
                order_number,
            ).fetchone()

            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO dbo.ExportBatchItems (
                        export_batch_id,
                        meter_id,
                        hls_key_id,
                        serial_number,
                        personalized_at
                    )
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    str(batch_row.export_batch_id),
                    str(row.meter_id),
                    str(row.hls_key_id),
                    row.serial_number,
                    row.personalized_at,
                )

                self._record_operation(
                    cursor,
                    str(row.meter_id),
                    OperationType.EXPORT_BATCH_CREATED,
                    (
                        "Счётчик включён в экспортную партию "
                        f"{batch_row.batch_number}."
                    ),
                )

            cursor.execute(
                """
                UPDATE dbo.ExportBatches
                SET row_count = ?
                WHERE export_batch_id = ?;
                """,
                len(rows),
                str(batch_row.export_batch_id),
            )

            connection.commit()

            return ExportBatch(
                id=UUID(str(batch_row.export_batch_id)),
                number=str(batch_row.batch_number),
                status=ExportBatchStatus(
                    str(batch_row.status)
                ),
                created_at=batch_row.created_at,
                created_by=str(batch_row.created_by),
                row_count=len(rows),
                customer_name=str(batch_row.customer_name),
                order_number=str(batch_row.order_number),
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_next_batch(
        self,
        created_by: str,
    ) -> ExportBatch:
        """Создаёт следующую партию из ещё не выгруженных HLS."""
        connection = open_connection()

        try:
            cursor = connection.cursor()
            prefix = datetime.now().astimezone().strftime(
                "HLS-%Y%m%d"
            )

            last_batch = cursor.execute(
                """
                SELECT TOP (1) batch_number
                FROM dbo.ExportBatches WITH (
                    UPDLOCK,
                    SERIALIZABLE
                )
                WHERE batch_number LIKE ?
                ORDER BY batch_number DESC;
                """,
                f"{prefix}-%",
            ).fetchone()

            sequence = 1

            if last_batch is not None:
                sequence = (
                    int(
                        str(last_batch.batch_number).rsplit(
                            "-",
                            1,
                        )[1]
                    )
                    + 1
                )

            batch_number = f"{prefix}-{sequence:03d}"

            batch_row = cursor.execute(
                """
                INSERT INTO dbo.ExportBatches (
                    batch_number,
                    created_by
                )
                OUTPUT
                    inserted.export_batch_id,
                    inserted.batch_number,
                    inserted.status,
                    inserted.created_at,
                    inserted.created_by,
                    inserted.row_count
                VALUES (?, ?);
                """,
                batch_number,
                created_by,
            ).fetchone()

            rows = cursor.execute(
                """
                SELECT
                    m.meter_id,
                    m.serial_number,
                    h.hls_key_id,
                    COALESCE(
                        activated.created_at,
                        h.created_at
                    ) AS personalized_at
                FROM dbo.Meters AS m WITH (
                    UPDLOCK,
                    SERIALIZABLE
                )
                INNER JOIN dbo.HlsKeys AS h WITH (
                    UPDLOCK,
                    SERIALIZABLE
                )
                    ON h.hls_key_id = m.current_hls_key_id
                OUTER APPLY (
                    SELECT TOP (1)
                        o.created_at
                    FROM dbo.Operations AS o
                    WHERE
                        o.meter_id = m.meter_id
                        AND o.operation_type = ?
                    ORDER BY o.created_at DESC
                ) AS activated
                WHERE
                    m.status = ?
                    AND h.status = ?
                    AND NOT EXISTS (
                        SELECT 1
                        FROM dbo.ExportBatchItems AS item
                        WHERE item.hls_key_id = h.hls_key_id
                    )
                ORDER BY m.serial_number;
                """,
                OperationType.HLS_ACTIVATED.value,
                MeterStatus.PERSONALIZED.value,
                HlsKeyStatus.ACTIVE.value,
            ).fetchall()

            if not rows:
                raise ValueError(
                    "Нет подтверждённых HLS для новой выгрузки."
                )

            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO dbo.ExportBatchItems (
                        export_batch_id,
                        meter_id,
                        hls_key_id,
                        serial_number,
                        personalized_at
                    )
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    str(batch_row.export_batch_id),
                    str(row.meter_id),
                    str(row.hls_key_id),
                    row.serial_number,
                    row.personalized_at,
                )

                self._record_operation(
                    cursor,
                    str(row.meter_id),
                    OperationType.EXPORT_BATCH_CREATED,
                    (
                        "ПУ включён в экспортную партию "
                        f"{batch_row.batch_number}."
                    ),
                )

            cursor.execute(
                """
                UPDATE dbo.ExportBatches
                SET row_count = ?
                WHERE export_batch_id = ?;
                """,
                len(rows),
                str(batch_row.export_batch_id),
            )

            connection.commit()

            return ExportBatch(
                id=UUID(str(batch_row.export_batch_id)),
                number=str(batch_row.batch_number),
                status=ExportBatchStatus(
                    str(batch_row.status)
                ),
                created_at=batch_row.created_at,
                created_by=str(batch_row.created_by),
                row_count=len(rows),
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_batch_items(
        self,
        batch: ExportBatch,
    ) -> list[tuple[ExportBatchItem, bytes]]:
        """Возвращает строки партии и защищённые HLS для Excel."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            rows = cursor.execute(
                """
                SELECT
                    item.export_batch_id,
                    item.meter_id,
                    item.hls_key_id,
                    item.serial_number,
                    item.personalized_at,
                    h.encrypted_value
                FROM dbo.ExportBatchItems AS item
                INNER JOIN dbo.HlsKeys AS h
                    ON h.hls_key_id = item.hls_key_id
                WHERE item.export_batch_id = ?
                ORDER BY item.serial_number;
                """,
                str(batch.id),
            ).fetchall()

            if len(rows) != batch.row_count:
                raise RuntimeError(
                    "Состав экспортной партии изменён или повреждён."
                )

            result: list[tuple[ExportBatchItem, bytes]] = []

            for row in rows:
                if row.encrypted_value is None:
                    raise RuntimeError(
                        "В партии отсутствует защищённое значение HLS."
                    )

                result.append(
                    (
                        ExportBatchItem(
                            batch_id=UUID(
                                str(row.export_batch_id)
                            ),
                            meter_id=UUID(str(row.meter_id)),
                            hls_key_id=UUID(
                                str(row.hls_key_id)
                            ),
                            serial_number=str(row.serial_number),
                            personalized_at=row.personalized_at,
                        ),
                        bytes(row.encrypted_value),
                    )
                )

            return result
        finally:
            connection.close()

    def complete_batch(
        self,
        batch: ExportBatch,
        file_name: str,
        file_sha256: str,
    ) -> None:
        """Фиксирует успешно созданный файл и его контрольную сумму."""
        if len(file_sha256) != 64:
            raise ValueError(
                "Контрольная сумма Excel-файла имеет неверный формат."
            )

        connection = open_connection()

        try:
            cursor = connection.cursor()

            changed_rows = cursor.execute(
                """
                UPDATE dbo.ExportBatches
                SET
                    status = ?,
                    file_name = ?,
                    file_sha256 = ?,
                    completed_at = SYSUTCDATETIME()
                WHERE
                    export_batch_id = ?
                    AND status = ?;
                """,
                ExportBatchStatus.COMPLETED.value,
                file_name,
                file_sha256,
                str(batch.id),
                ExportBatchStatus.CREATED.value,
            ).rowcount

            if changed_rows != 1:
                raise RuntimeError(
                    "Экспортная партия уже завершена или не найдена."
                )

            rows = cursor.execute(
                """
                SELECT meter_id
                FROM dbo.ExportBatchItems
                WHERE export_batch_id = ?;
                """,
                str(batch.id),
            ).fetchall()

            for row in rows:
                self._record_operation(
                    cursor,
                    str(row.meter_id),
                    OperationType.EXPORT_BATCH_COMPLETED,
                    (
                        "Excel-файл экспортной партии "
                        f"{batch.number} успешно создан."
                    ),
                )

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _record_operation(
        cursor: pyodbc.Cursor,
        meter_id: str,
        operation_type: OperationType,
        description: str,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO dbo.Operations (
                meter_id,
                operation_type,
                description
            )
            VALUES (?, ?, ?);
            """,
            meter_id,
            operation_type.value,
            description,
        )