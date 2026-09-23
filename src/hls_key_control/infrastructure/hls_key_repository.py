"""Репозиторий HLS-ключей в SQL Server."""

from uuid import UUID

from hls_key_control.domain.models import (
    HlsKey,
    HlsKeyStatus,
    Meter,
    MeterStatus,
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

    def confirm_reset_for_test(self, meter: Meter) -> None:
        """Тестово отзывает HLS и переводит ПУ в состояние после сброса."""
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

            connection.commit()

            meter.current_hls_key_id = None
            meter.status = MeterStatus.RESET
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()