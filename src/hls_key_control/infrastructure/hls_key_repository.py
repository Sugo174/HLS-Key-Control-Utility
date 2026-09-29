"""Репозиторий HLS-ключей в SQL Server."""

from uuid import UUID

from hls_key_control.domain.models import (
    HlsHistoryRecord,
    HlsKey,
    HlsKeyStatus,
    HlsKeyPurpose,
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

    def reserve_new_key(
        self,
        meter: Meter,
        purpose: HlsKeyPurpose = HlsKeyPurpose.PERSONALIZATION,
    ) -> tuple[HlsKey, str]:
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
            active_service_key_row = None

            if (
                database_status == MeterStatus.PERSONALIZED
                and meter_row.current_hls_key_id is not None
            ):
                active_service_key_row = cursor.execute(
                    """
                    SELECT hls_key_id
                    FROM dbo.HlsKeys WITH (UPDLOCK, SERIALIZABLE)
                    WHERE
                        hls_key_id = ?
                        AND purpose = ?
                        AND status = ?;
                    """,
                    str(meter_row.current_hls_key_id),
                    HlsKeyPurpose.SERVICE.value,
                    HlsKeyStatus.ACTIVE.value,
                ).fetchone()

            can_replace_service_hls = (
                active_service_key_row is not None
            )

            if (
                database_status not in {
                    MeterStatus.NEW,
                    MeterStatus.RESET,
                }
                and not can_replace_service_hls
            ):
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
                    encrypted_value,
                    purpose
                )
                OUTPUT
                    inserted.hls_key_id,
                    inserted.status,
                    inserted.fingerprint,
                    inserted.purpose,
                    inserted.created_at
                VALUES (?, ?, ?, ?, ?);
                """,
                str(meter_row.meter_id),
                HlsKeyStatus.RESERVED.value,
                fingerprint,
                encrypted_value,
                purpose.value,
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
                purpose=HlsKeyPurpose(str(key_row.purpose)),
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

    def reserve_service_key(
        self,
        meter: Meter,
    ) -> tuple[HlsKey, str]:
        """Резервирует сервисный HLS, не затрагивая текущий ключ ПУ."""
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
                raise ValueError("Счётчик отсутствует в базе данных.")

            database_status = MeterStatus(str(meter_row.status))

            if database_status in {
                MeterStatus.OPERATION_UNCONFIRMED,
                MeterStatus.HLS_STATE_UNKNOWN,
            }:
                raise ValueError(
                    "Для счётчика есть незавершённая операция HLS. "
                    "Сначала проверьте состояние HLS."
                )

            pending_key = cursor.execute(
                """
                SELECT TOP (1) hls_key_id
                FROM dbo.HlsKeys
                WHERE
                    meter_id = ?
                    AND purpose = ?
                    AND status IN (?, ?);
                """,
                str(meter_row.meter_id),
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.RESERVED.value,
                HlsKeyStatus.UNCONFIRMED.value,
            ).fetchone()

            if pending_key is not None:
                raise ValueError(
                    "Для счётчика уже есть сервисный HLS, "
                    "ожидающий завершения операции."
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
                    encrypted_value,
                    purpose
                )
                OUTPUT
                    inserted.hls_key_id,
                    inserted.status,
                    inserted.fingerprint,
                    inserted.purpose,
                    inserted.created_at
                VALUES (?, ?, ?, ?, ?);
                """,
                str(meter_row.meter_id),
                HlsKeyStatus.RESERVED.value,
                fingerprint,
                encrypted_value,
                HlsKeyPurpose.SERVICE.value,
            ).fetchone()

            self._record_operation(
                cursor,
                str(meter_row.meter_id),
                OperationType.HLS_RESERVED,
                "Сервисный HLS сформирован и зарезервирован.",
            )

            connection.commit()

            return (
                HlsKey(
                    id=UUID(str(key_row.hls_key_id)),
                    meter_serial_number=meter.serial_number,
                    status=HlsKeyStatus(str(key_row.status)),
                    fingerprint=str(key_row.fingerprint),
                    purpose=HlsKeyPurpose(str(key_row.purpose)),
                    created_at=key_row.created_at,
                ),
                hls_password,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_reserved_service_hls_password(
        self,
        meter: Meter,
    ) -> str:
        """Возвращает сервисный HLS только для первой попытки записи."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT TOP (1) h.encrypted_value
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.meter_id = m.meter_id
                WHERE
                    m.serial_number = ?
                    AND h.purpose = ?
                    AND h.status = ?
                ORDER BY h.created_at DESC;
                """,
                meter.serial_number,
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.RESERVED.value,
            ).fetchone()

            if row is None or row.encrypted_value is None:
                raise ValueError(
                    "Нет сервисного HLS, готового к записи."
                )

            return self._protector.decrypt(
                bytes(row.encrypted_value)
            )
        finally:
            connection.close()

    def has_unconfirmed_service_hls(
        self,
        meter: Meter,
    ) -> bool:
        """Проверяет, ожидает ли сервисный HLS подтверждения."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT 1
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.meter_id = m.meter_id
                WHERE
                    m.serial_number = ?
                    AND h.purpose = ?
                    AND h.status = ?;
                """,
                meter.serial_number,
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.UNCONFIRMED.value,
            ).fetchone()

            return row is not None
        finally:
            connection.close()

    def get_hls_history_by_password(
        self,
        hls_password: str,
    ) -> list[HlsHistoryRecord]:
        """Находит счётчик по HLS и возвращает всю его историю."""
        normalized_hls_password = hls_password.strip()

        if not normalized_hls_password:
            raise ValueError("Укажите HLS-пароль.")

        fingerprint = self._protector.create_fingerprint(
            normalized_hls_password
        )
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT m.serial_number
                FROM dbo.HlsKeys AS h
                INNER JOIN dbo.Meters AS m
                    ON m.meter_id = h.meter_id
                WHERE h.fingerprint = ?;
                """,
                fingerprint,
            ).fetchone()
        finally:
            connection.close()

        if row is None:
            return []

        return self.get_hls_history_by_serial_number(
            str(row.serial_number)
        )

    def get_hls_history_by_serial_number(
        self,
        meter_serial_number: str,
    ) -> list[HlsHistoryRecord]:
        """Возвращает полную историю HLS для указанного счётчика."""
        normalized_serial_number = meter_serial_number.strip()

        if not normalized_serial_number:
            raise ValueError("Укажите серийный номер счётчика.")

        connection = open_connection()

        try:
            cursor = connection.cursor()

            rows = cursor.execute(
                """
                SELECT
                    m.serial_number,
                    h.status,
                    h.purpose,
                    h.encrypted_value,
                    h.created_at,
                    h.retired_at
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.meter_id = m.meter_id
                WHERE m.serial_number = ?
                ORDER BY h.created_at DESC;
                """,
                normalized_serial_number,
            ).fetchall()

            history: list[HlsHistoryRecord] = []

            for row in rows:
                hls_password = None

                if row.encrypted_value is not None:
                    hls_password = self._protector.decrypt(
                        bytes(row.encrypted_value)
                    )

                history.append(
                    HlsHistoryRecord(
                        meter_serial_number=str(row.serial_number),
                        hls_password=hls_password,
                        status=HlsKeyStatus(str(row.status)),
                        purpose=HlsKeyPurpose(str(row.purpose)),
                        created_at=row.created_at,
                        retired_at=row.retired_at,
                    )
                )

            return history
        finally:
            connection.close()

    def get_unconfirmed_service_hls_password(
        self,
        meter: Meter,
    ) -> str:
        """Возвращает сервисный HLS только для проверки его состояния."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT TOP (1) h.encrypted_value
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.meter_id = m.meter_id
                WHERE
                    m.serial_number = ?
                    AND h.purpose = ?
                    AND h.status = ?
                ORDER BY h.created_at DESC;
                """,
                meter.serial_number,
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.UNCONFIRMED.value,
            ).fetchone()

            if row is None or row.encrypted_value is None:
                raise ValueError(
                    "Нет неподтверждённого сервисного HLS "
                    "для проверки."
                )

            return self._protector.decrypt(
                bytes(row.encrypted_value)
            )
        finally:
            connection.close()

    def has_active_service_hls(
        self,
        meter: Meter,
    ) -> bool:
        """Проверяет, является ли текущий HLS сервисным."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT 1
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.hls_key_id = m.current_hls_key_id
                WHERE
                    m.serial_number = ?
                    AND h.purpose = ?
                    AND h.status = ?;
                """,
                meter.serial_number,
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.ACTIVE.value,
            ).fetchone()

            return row is not None
        finally:
            connection.close()


    def get_active_service_hls_password(
        self,
        meter: Meter,
    ) -> str:
        """Возвращает подтверждённый сервисный HLS из реестра."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT h.encrypted_value
                FROM dbo.Meters AS m
                INNER JOIN dbo.HlsKeys AS h
                    ON h.hls_key_id = m.current_hls_key_id
                WHERE
                    m.serial_number = ?
                    AND h.purpose = ?
                    AND h.status = ?;
                """,
                meter.serial_number,
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.ACTIVE.value,
            ).fetchone()

            if row is None or row.encrypted_value is None:
                raise ValueError(
                    "В реестре нет подтверждённого сервисного HLS "
                    "для этого счётчика."
                )

            return self._protector.decrypt(
                bytes(row.encrypted_value)
            )
        finally:
            connection.close()

    def cancel_reserved_service_hls_before_write(
        self,
        meter: Meter,
    ) -> None:
        """Отменяет сервисный резерв, если запись в счётчик не началась."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            meter_row = cursor.execute(
                """
                SELECT meter_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if meter_row is None:
                raise ValueError("Счётчик отсутствует в базе данных.")

            key_row = cursor.execute(
                """
                SELECT TOP (1) hls_key_id
                FROM dbo.HlsKeys WITH (UPDLOCK, SERIALIZABLE)
                WHERE
                    meter_id = ?
                    AND purpose = ?
                    AND status = ?
                ORDER BY created_at DESC;
                """,
                str(meter_row.meter_id),
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.RESERVED.value,
            ).fetchone()

            if key_row is None:
                raise ValueError(
                    "Нет сервисного HLS, ожидающего отмены."
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
                str(key_row.hls_key_id),
                HlsKeyStatus.RESERVED.value,
            ).rowcount

            if changed_keys != 1:
                raise RuntimeError(
                    "Не удалось отменить сервисный резерв HLS."
                )

            self._record_operation(
                cursor,
                str(meter_row.meter_id),
                OperationType.HLS_WRITE_REJECTED,
                "Команда записи сервисного HLS не отправлялась; "
                "резерв отозван.",
            )

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_service_hls_write_unconfirmed(
        self,
        meter: Meter,
    ) -> None:
        """Фиксирует сервисную запись HLS с неизвестным результатом."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            meter_row = cursor.execute(
                """
                SELECT meter_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if meter_row is None:
                raise ValueError("Счётчик отсутствует в базе данных.")

            key_row = cursor.execute(
                """
                SELECT TOP (1) hls_key_id
                FROM dbo.HlsKeys WITH (UPDLOCK, SERIALIZABLE)
                WHERE
                    meter_id = ?
                    AND purpose = ?
                    AND status = ?
                ORDER BY created_at DESC;
                """,
                str(meter_row.meter_id),
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.RESERVED.value,
            ).fetchone()

            if key_row is None:
                raise ValueError(
                    "Нет сервисного HLS для фиксации "
                    "неподтверждённой записи."
                )

            changed_keys = cursor.execute(
                """
                UPDATE dbo.HlsKeys
                SET status = ?
                WHERE
                    hls_key_id = ?
                    AND status = ?;
                """,
                HlsKeyStatus.UNCONFIRMED.value,
                str(key_row.hls_key_id),
                HlsKeyStatus.RESERVED.value,
            ).rowcount

            if changed_keys != 1:
                raise RuntimeError(
                    "Не удалось зафиксировать "
                    "неподтверждённую сервисную запись HLS."
                )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET status = ?
                WHERE meter_id = ?;
                """,
                MeterStatus.HLS_STATE_UNKNOWN.value,
                str(meter_row.meter_id),
            )

            self._record_operation(
                cursor,
                str(meter_row.meter_id),
                OperationType.HLS_WRITE_UNCONFIRMED,
                "Запись сервисного HLS не подтверждена. "
                "Требуется проверка состояния HLS.",
            )

            connection.commit()
            meter.status = MeterStatus.HLS_STATE_UNKNOWN
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def confirm_service_hls_written(
        self,
        meter: Meter,
    ) -> None:
        """Подтверждает сервисный HLS после успешной проверки входа."""
        connection = open_connection()

        try:
            cursor = connection.cursor()

            meter_row = cursor.execute(
                """
                SELECT meter_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if meter_row is None:
                raise ValueError("Счётчик отсутствует в базе данных.")

            key_row = cursor.execute(
                """
                SELECT TOP (1)
                    hls_key_id,
                    status
                FROM dbo.HlsKeys WITH (UPDLOCK, SERIALIZABLE)
                WHERE
                    meter_id = ?
                    AND purpose = ?
                    AND status IN (?, ?)
                ORDER BY created_at DESC;
                """,
                str(meter_row.meter_id),
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.RESERVED.value,
                HlsKeyStatus.UNCONFIRMED.value,
            ).fetchone()

            if key_row is None:
                raise ValueError(
                    "Нет сервисного HLS, ожидающего подтверждения."
                )

            changed_keys = cursor.execute(
                """
                UPDATE dbo.HlsKeys
                SET status = ?
                WHERE
                    hls_key_id = ?
                    AND status IN (?, ?);
                """,
                HlsKeyStatus.ACTIVE.value,
                str(key_row.hls_key_id),
                HlsKeyStatus.RESERVED.value,
                HlsKeyStatus.UNCONFIRMED.value,
            ).rowcount

            if changed_keys != 1:
                raise RuntimeError(
                    "Не удалось подтвердить сервисный HLS."
                )

            cursor.execute(
                """
                UPDATE dbo.HlsKeys
                SET
                    status = ?,
                    retired_at = SYSUTCDATETIME()
                WHERE
                    meter_id = ?
                    AND status = ?
                    AND hls_key_id <> ?;
                """,
                HlsKeyStatus.RETIRED.value,
                str(meter_row.meter_id),
                HlsKeyStatus.ACTIVE.value,
                str(key_row.hls_key_id),
            )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET
                    current_hls_key_id = ?,
                    status = ?
                WHERE meter_id = ?;
                """,
                str(key_row.hls_key_id),
                MeterStatus.PERSONALIZED.value,
                str(meter_row.meter_id),
            )

            self._record_operation(
                cursor,
                str(meter_row.meter_id),
                OperationType.HLS_ACTIVATED,
                "Сервисный HLS записан в счётчик и подтверждён входом.",
            )

            connection.commit()

            meter.current_hls_key_id = UUID(
                str(key_row.hls_key_id)
            )
            meter.status = MeterStatus.PERSONALIZED
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
                UPDATE dbo.HlsKeys
                SET
                    status = ?,
                    retired_at = SYSUTCDATETIME()
                WHERE
                    meter_id = ?
                    AND hls_key_id <> ?
                    AND status = ?;
                """,
                HlsKeyStatus.RETIRED.value,
                str(row.meter_id),
                str(row.current_hls_key_id),
                HlsKeyStatus.ACTIVE.value,
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

            previous_service_key_row = cursor.execute(
                """
                SELECT TOP (1) hls_key_id
                FROM dbo.HlsKeys
                WHERE
                    meter_id = ?
                    AND hls_key_id <> ?
                    AND purpose = ?
                    AND status = ?
                ORDER BY created_at DESC;
                """,
                str(row.meter_id),
                str(row.current_hls_key_id),
                HlsKeyPurpose.SERVICE.value,
                HlsKeyStatus.ACTIVE.value,
            ).fetchone()

            if previous_service_key_row is not None:
                restored_hls_key_id = str(
                    previous_service_key_row.hls_key_id
                )
                restored_status = MeterStatus.PERSONALIZED.value
                description = (
                    "Команда записи персонализационного HLS "
                    "не отправлялась; сервисный HLS восстановлен "
                    "в реестре."
                )
            else:
                restored_hls_key_id = None
                restored_status = MeterStatus.RESET.value
                description = (
                    "Команда записи HLS не отправлялась; "
                    "резерв отозван."
                )

            cursor.execute(
                """
                UPDATE dbo.Meters
                SET
                    current_hls_key_id = ?,
                    status = ?
                WHERE meter_id = ?;
                """,
                restored_hls_key_id,
                restored_status,
                str(row.meter_id),
            )

            self._record_operation(
                cursor,
                str(row.meter_id),
                OperationType.HLS_WRITE_REJECTED,
                description,
            )

            connection.commit()

            if previous_service_key_row is not None:
                meter.current_hls_key_id = UUID(
                    str(previous_service_key_row.hls_key_id)
                )
                meter.status = MeterStatus.PERSONALIZED
            else:
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
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                meter.serial_number,
            ).fetchone()

            if row is None:
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

            if row.current_hls_key_id is None:
                description = (
                    "Связь при сбросе HLS не подтверждена. "
                    "Предыдущий HLS в реестре отсутствует; "
                    "требуется проверка состояния HLS."
                )
            else:
                description = (
                    "Связь при сбросе HLS не подтверждена. "
                    "Требуется проверка состояния HLS."
                )

            self._record_operation(
                cursor,
                str(row.meter_id),
                OperationType.HLS_RESET,
                description,
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
                raise ValueError("Счётчик отсутствует в базе данных.")

            if meter_row.current_hls_key_id is not None:
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
                        "Не удалось отозвать текущий HLS "
                        "в базе данных."
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

            if description is None:
                if meter_row.current_hls_key_id is None:
                    description = (
                        "HLS сброшен до заводского. "
                        "Предыдущий HLS в реестре отсутствовал."
                    )
                else:
                    description = (
                        "HLS сброшен, предыдущий ключ отозван."
                    )

            self._record_operation(
                cursor,
                str(meter_row.meter_id),
                OperationType.HLS_RESET,
                description,
            )

            connection.commit()

            meter.current_hls_key_id = None
            meter.status = MeterStatus.RESET
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()