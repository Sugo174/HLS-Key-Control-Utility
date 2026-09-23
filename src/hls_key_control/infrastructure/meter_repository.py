"""Репозиторий счётчиков в SQL Server."""

from uuid import UUID

import pyodbc

from hls_key_control.domain.models import Meter, MeterStatus
from hls_key_control.infrastructure.sql_server import open_connection


class SqlMeterRepository:
    """Читает и создаёт карточки ПУ в базе HlsKeyControl."""

    def find_by_serial(self, serial_number: str) -> Meter | None:
        """Возвращает ПУ по серийному номеру без изменения базы."""
        normalized_serial = self._normalize_serial(serial_number)

        connection = open_connection()

        try:
            cursor = connection.cursor()
            row = cursor.execute(
                """
                SELECT
                    meter_id,
                    serial_number,
                    status,
                    created_at,
                    current_hls_key_id
                FROM dbo.Meters
                WHERE serial_number = ?;
                """,
                normalized_serial,
            ).fetchone()

            if row is None:
                return None

            return self._map_meter(row)
        finally:
            connection.close()

    def get_or_create(self, serial_number: str) -> tuple[Meter, bool]:
        """Возвращает ПУ или безопасно создаёт его при первом считывании."""
        normalized_serial = self._normalize_serial(serial_number)
        connection = open_connection()

        try:
            cursor = connection.cursor()

            row = cursor.execute(
                """
                SELECT
                    meter_id,
                    serial_number,
                    status,
                    created_at,
                    current_hls_key_id
                FROM dbo.Meters WITH (UPDLOCK, SERIALIZABLE)
                WHERE serial_number = ?;
                """,
                normalized_serial,
            ).fetchone()

            if row is not None:
                connection.commit()
                return self._map_meter(row), False

            created_row = cursor.execute(
                """
                INSERT INTO dbo.Meters (serial_number)
                OUTPUT
                    inserted.meter_id,
                    inserted.serial_number,
                    inserted.status,
                    inserted.created_at,
                    inserted.current_hls_key_id
                VALUES (?);
                """,
                normalized_serial,
            ).fetchone()

            connection.commit()
            return self._map_meter(created_row), True
        except pyodbc.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _normalize_serial(serial_number: str) -> str:
        normalized_serial = serial_number.strip().upper()

        if not normalized_serial:
            raise ValueError("Серийный номер не может быть пустым.")

        return normalized_serial

    @staticmethod
    def _map_meter(row: pyodbc.Row) -> Meter:
        return Meter(
            serial_number=str(row.serial_number),
            status=MeterStatus(str(row.status)),
            created_at=row.created_at,
            current_hls_key_id=(
                UUID(str(row.current_hls_key_id))
                if row.current_hls_key_id is not None
                else None
            ),
        )