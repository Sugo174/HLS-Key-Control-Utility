"""Репозиторий HLS-ключей в SQL Server."""

from uuid import UUID

from hls_key_control.domain.models import (
    HlsKey,
    HlsKeyStatus,
    Meter,
    MeterStatus,
    OperationType,
)
from hls_key_control.infrastructure.sql_server import open_connection
from hls_key_control.services.hls_generator import generate_hls_password
from hls_key_control.services.hls_protection import HlsProtector


class SqlHlsKeyRepository:
    """Резервирует и отзывает HLS с сохранением состояния в SQL Server."""

    def __init__(self) -> None:
        self._protector = HlsProtector()

    def reserve_new_key(self, meter: Meter) -> tuple[HlsKey, str]:
        """Создаёт HLS, шифрует его и сохраняет резерв в одной транзакции."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            meter_row = cursor.execute(
                """
                SELECT
                    meter_id,
                    status,
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if meter_row is None:
                raise ValueError("ПУ отсутствует в базе данных.")

            database_status = MeterStatus(str(meter_row.status))

            if database_status not in {
                MeterStatus.NEW,
                MeterStatus.RESET,
            }:
                raise ValueError(
                    "Для данного ПУ нельзя создать новый HLS "
                    "в текущем статусе."
                )

            hls_password = generate_hls_password()
            fingerprint = self._protector.create_fingerprint(hls_password)
            encrypted_value = self._protector.encrypt(hls_password)

            key_row = cursor.execute(
                """
                INSERT INTO dbo.HlsKeys (
                    meter_id,
                    status,
                    fingerprint,
                    encrypted_value
                )
                OUTPUT
                    inserted.hls_key_id,
                    inserted.status,
                    inserted.fingerprint,
                    inserted.created_at
                VALUES (?, ?, ?, ?);
                """,
                str(meter_row.meter_id),
                HlsKeyStatus.RESERVED.value,
                fingerprint,
                encrypted_value,
            ).fetchone()

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET
                    current_hls_key_id = ?,
                    status = ?
                WHERE meter_id = ?;
                """,
                str(key_row.hls_key_id),
                MeterStatus.OPERATION_UNCONFIRMED.value,
                str(meter_row.meter_id),
            )

            self._record_operation(
                cursor,
                str(meter_row.meter_id),
                OperationType.HLS_RESERVED,
                "Новый HLS сформирован и зарезервирован.",
            )

            connection.commit()

            key = HlsKey(
                id=UUID(str(key_row.hls_key_id)),
                meter_serial_number=meter.serial_number,
                status=HlsKeyStatus(str(key_row.status)),
                fingerprint=str(key_row.fingerprint),
                created_at=key_row.created_at,
            )

            meter.current_hls_key_id = key.id
            meter.status = MeterStatus.OPERATION_UNCONFIRMED

            return key, hls_password
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_reserved_hls_password(self, meter: Meter) -> str:
        """Возвращает расшифрованный резерв только для записи в ПУ."""
        connection = open_connection()

        try:
            cursor = connection.cursor()
            row = cursor.execute(
                """
                SELECT
                    m.status AS meter_status,
                    m.current_hls_key_id,
                    h.status AS hls_status,
                    h.encrypted_value
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.hls_key_id = m.current_hls_key_id
                WHERE m.serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if row is None:
                raise ValueError(
                    "Для ПУ нет зарезервированного HLS."
                )

            if (
                str(row.meter_status)
                != MeterStatus.OPERATION_UNCONFIRMED.value
                or str(row.hls_status)
                != HlsKeyStatus.RESERVED.value
            ):
                raise ValueError(
                    "HLS не находится в состоянии ожидания записи."
                )

            if row.encrypted_value is None:
                raise RuntimeError(
                    "В резерве HLS отсутствует защищённое значение."
                )

            return self._protector.decrypt(
                bytes(row.encrypted_value)
            )
        finally:
            connection.close()

    def get_unconfirmed_hls_password(self, meter: Meter) -> str:
        """Возвращает HLS для безопасной проверки неподтверждённой записи."""
        connection = open_connection()

        try:
            cursor = connection.cursor()
            row = cursor.execute(
                """
                SELECT
                    m.status AS meter_status,
                    h.status AS hls_status,
                    h.encrypted_value
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.hls_key_id = m.current_hls_key_id
                WHERE m.serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if (
                row is None
                or str(row.meter_status)
                != MeterStatus.HLS_STATE_UNKNOWN.value
                or str(row.hls_status)
                != HlsKeyStatus.UNCONFIRMED.value
                or row.encrypted_value is None
            ):
                raise ValueError(
                    "Для ПУ нет неподтверждённого HLS."
                )

            return self._protector.decrypt(
                bytes(row.encrypted_value)
            )
        finally:
            connection.close()

    def confirm_hls_written(self, meter: Meter) -> None:
        """Подтверждает HLS только после успешной проверки входа в ПУ."""
        connection = open_connection()

        try:
            cursor = connection.cursor()
            row = cursor.execute(
                """
                SELECT
                    meter_id,
                    status,
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if (
                row is None
                or row.current_hls_key_id is None
                or str(row.status) not in {
                    MeterStatus.OPERATION_UNCONFIRMED.value,
                    MeterStatus.HLS_STATE_UNKNOWN.value,
                }
            ):
                raise ValueError(
                    "Нет HLS, ожидающего подтверждения записи."
                )

            expected_hls_status = (
                HlsKeyStatus.RESERVED.value
                if str(row.status)
                == MeterStatus.OPERATION_UNCONFIRMED.value
                else HlsKeyStatus.UNCONFIRMED.value
            )

            changed_keys = cursor.execute(
                """
                UPDATE dbo.HlsKeys
                SET status = ?
                WHERE
                    hls_key_id = ?
                    AND status = ?;
                """,
                HlsKeyStatus.ACTIVE.value,
                str(row.current_hls_key_id),
                expected_hls_status,
            ).rowcount

            if changed_keys != 1:
                raise RuntimeError(
                    "Не удалось подтвердить статус HLS."
                )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET status = ?
                WHERE meter_id = ?;
                """,
                MeterStatus.PERSONALIZED.value,
                str(row.meter_id),
            )

            self._record_operation(
                cursor,
                str(row.meter_id),
                OperationType.HLS_ACTIVATED,
                "Новый HLS записан в ПУ и подтверждён входом.",
            )

            connection.commit()
            meter.status = MeterStatus.PERSONALIZED
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def cancel_reserved_hls_before_write(
        self,
        meter: Meter,
    ) -> None:
        """Отменяет резерв HLS, если команда записи в ПУ не отправлялась."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT
                    meter_id,
                    status,
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if (
                row is None
                or row.current_hls_key_id is None
                or str(row.status)
                != MeterStatus.OPERATION_UNCONFIRMED.value
            ):
                raise ValueError(
                    "Нет резерва HLS, ожидающего отмены."
                )

            changed_keys = cursor.execute(
                """
                UPDATE dbo.HlsKeys
                SET status = ?
                WHERE
                    hls_key_id = ?
                    AND status = ?;
                """,
                HlsKeyStatus.FAILED.value,
                str(row.current_hls_key_id),
                HlsKeyStatus.RESERVED.value,
            ).rowcount

            if changed_keys != 1:
                raise RuntimeError(
                    "Не удалось отменить резерв HLS."
                )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET
                    current_hls_key_id = NULL,
                    status = ?
                WHERE meter_id = ?;
                """,
                MeterStatus.RESET.value,
                str(row.meter_id),
            )

            self._record_operation(
                cursor,
                str(row.meter_id),
                OperationType.HLS_WRITE_REJECTED,
                "Команда записи HLS не отправлялась; резерв отозван.",
            )

            connection.commit()
            meter.current_hls_key_id = None
            meter.status = MeterStatus.RESET
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_hls_write_unconfirmed(self, meter: Meter) -> None:
        """Блокирует повторную запись после сбоя физической операции."""
        connection = open_connection()

        try:
            cursor = connection.cursor()
            row = cursor.execute(
                """
                SELECT
                    meter_id,
                    status,
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if (
                row is None
                or row.current_hls_key_id is None
                or str(row.status)
                != MeterStatus.OPERATION_UNCONFIRMED.value
            ):
                raise ValueError(
                    "Нет HLS, для которого можно зафиксировать сбой."
                )

            cursor.execute(
                """
                UPDATE dbo.HlsKeys
                SET status = ?
                WHERE
                    hls_key_id = ?
                    AND status = ?;
                """,
                HlsKeyStatus.UNCONFIRMED.value,
                str(row.current_hls_key_id),
                HlsKeyStatus.RESERVED.value,
            )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET status = ?
                WHERE meter_id = ?;
                """,
                MeterStatus.HLS_STATE_UNKNOWN.value,
                str(row.meter_id),
            )

            self._record_operation(
                cursor,
                str(row.meter_id),
                OperationType.HLS_WRITE_UNCONFIRMED,
                "Связь при записи HLS не подтверждена. "
                "Повторная запись заблокирована.",
            )

            connection.commit()
            meter.status = MeterStatus.HLS_STATE_UNKNOWN
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


    def confirm_hls_write_rejected(self, meter: Meter) -> None:
        """Фиксирует подтверждённый отказ ПУ без повторной записи."""
        connection = open_connection()

        try:
            cursor = connection.cursor()
            row = cursor.execute(
                """
                SELECT
                    meter_id,
                    status,
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if (
                row is None
                or row.current_hls_key_id is None
                or str(row.status)
                != MeterStatus.HLS_STATE_UNKNOWN.value
            ):
                raise ValueError(
                    "Нет неподтверждённой записи HLS для восстановления."
                )

            changed_keys = cursor.execute(
                """
                UPDATE dbo.HlsKeys
                SET status = ?
                WHERE
                    hls_key_id = ?
                    AND status = ?;
                """,
                HlsKeyStatus.FAILED.value,
                str(row.current_hls_key_id),
                HlsKeyStatus.UNCONFIRMED.value,
            ).rowcount

            if changed_keys != 1:
                raise RuntimeError(
                    "Не удалось зафиксировать отказ записи HLS."
                )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET
                    current_hls_key_id = NULL,
                    status = ?
                WHERE meter_id = ?;
                """,
                MeterStatus.RESET.value,
                str(row.meter_id),
            )

            self._record_operation(
                cursor,
                str(row.meter_id),
                OperationType.HLS_WRITE_REJECTED,
                "ПУ отклонил запись HLS. Прежний HLS подтверждён.",
            )

            connection.commit()
            meter.current_hls_key_id = None
            meter.status = MeterStatus.RESET
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_reset_unconfirmed(self, meter: Meter) -> None:
        """Блокирует действия после неподтверждённого сброса HLS."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT
                    meter_id,
                    status,
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if (
                row is None
                or row.current_hls_key_id is None
                or str(row.status)
                != MeterStatus.PERSONALIZED.value
            ):
                raise ValueError(
                    "Для счётчика нельзя зафиксировать "
                    "неподтверждённый сброс HLS."
                )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET status = ?
                WHERE meter_id = ?;
                """,
                MeterStatus.HLS_STATE_UNKNOWN.value,
                str(row.meter_id),
            )

            self._record_operation(
                cursor,
                str(row.meter_id),
                OperationType.HLS_RESET,
                "Связь при сбросе HLS не подтверждена. "
                "Требуется проверка состояния HLS.",
            )

            connection.commit()
            meter.status = MeterStatus.HLS_STATE_UNKNOWN
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_active_hls_password_for_reset_recovery(
        self,
        meter: Meter,
    ) -> str:
        """Возвращает активный HLS только для проверки после сбоя сброса."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT
                    m.status AS meter_status,
                    h.status AS hls_status,
                    h.encrypted_value
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.hls_key_id = m.current_hls_key_id
                WHERE m.serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if (
                row is None
                or str(row.meter_status)
                != MeterStatus.HLS_STATE_UNKNOWN.value
                or str(row.hls_status)
                != HlsKeyStatus.ACTIVE.value
                or row.encrypted_value is None
            ):
                raise ValueError(
                    "Для счётчика нет активного HLS "
                    "для проверки состояния."
                )

            return self._protector.decrypt(
                bytes(row.encrypted_value)
            )
        finally:
            connection.close()

    def confirm_active_hls_after_reset_check(
        self,
        meter: Meter,
    ) -> None:
        """Возвращает статус active после успешной проверки HLS."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT
                    m.meter_id,
                    m.status,
                    h.status AS hls_status
                FROM dbo.Meters AS m WITH (
                    UPDLOCK,
                    SERIALIZABLE
                )
                INNER JOIN dbo.HlsKeys AS h WITH (
                    UPDLOCK,
                    SERIALIZABLE
                )
                    ON h.hls_key_id = m.current_hls_key_id
                WHERE m.serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if (
                row is None
                or str(row.status)
                != MeterStatus.HLS_STATE_UNKNOWN.value
                or str(row.hls_status)
                != HlsKeyStatus.ACTIVE.value
            ):
                raise ValueError(
                    "Состояние счётчика нельзя подтвердить "
                    "как персонализированное."
                )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET status = ?
                WHERE meter_id = ?;
                """,
                MeterStatus.PERSONALIZED.value,
                str(row.meter_id),
            )

            self._record_operation(
                cursor,
                str(row.meter_id),
                OperationType.HLS_RESET,
                "Проверка состояния HLS: персональный HLS "
                "подтверждён, сброс не состоялся.",
            )

            connection.commit()
            meter.status = MeterStatus.PERSONALIZED
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _record_operation(
        cursor,
        meter_id: str,
        operation_type: OperationType,
        description: str,
    ) -> None:
        """Добавляет аудит в рамках текущей SQL-транзакции."""
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


    def confirm_physical_reset(
        self,
        meter: Meter,
        description: str | None = None,
    ) -> None:
        """Фиксирует сброс только после проверки заводского HLS в ПУ."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            meter_row = cursor.execute(
                """
                SELECT
                    meter_id,
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if meter_row is None:
                raise ValueError("ПУ отсутствует в базе данных.")

            if meter_row.current_hls_key_id is None:
                raise ValueError("У данного ПУ нет HLS для отзыва.")

            changed_rows = cursor.execute(
                """
                UPDATE dbo.HlsKeys
                SET
                    status = ?,
                    retired_at = SYSUTCDATETIME()
                WHERE hls_key_id = ?;
                """,
                HlsKeyStatus.RETIRED.value,
                str(meter_row.current_hls_key_id),
            ).rowcount

            if changed_rows != 1:
                raise RuntimeError(
                    "Не удалось отозвать текущий HLS в базе данных."
                )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET
                    current_hls_key_id = NULL,
                    status = ?
                WHERE meter_id = ?;
                """,
                MeterStatus.RESET.value,
                str(meter_row.meter_id),
            )

            self._record_operation(
                cursor,
                str(meter_row.meter_id),
                OperationType.HLS_RESET,
                description or (
                    "HLS сброшен, предыдущий ключ отозван."
                ),
            )

            connection.commit()

            meter.current_hls_key_id = None
            meter.status = MeterStatus.RESET
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()