"""Модели предметной области HLS Key Control Utility."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


class MeterStatus(StrEnum):
    """Состояния прибора в производственном процессе."""

    NEW = "new"
    PERSONALIZED = "personalized"
    OPERATION_UNCONFIRMED = "operation_unconfirmed"
    RESET = "reset"
    HLS_STATE_UNKNOWN = "hls_state_unknown"


class HlsKeyStatus(StrEnum):
    """Состояния созданного HLS-пароля."""

    RESERVED = "reserved"
    ACTIVE = "active"
    RETIRED = "retired"
    UNCONFIRMED = "unconfirmed"
    FAILED = "failed"


class ExportBatchStatus(StrEnum):
    """Состояния экспортной партии."""

    CREATED = "created"
    COMPLETED = "completed"
    FAILED = "failed"    


class OperationType(StrEnum):
    """Типы операций, выполняемых с прибором."""

    METER_REGISTERED = "meter_registered"
    METER_FOUND = "meter_found"
    HLS_RESERVED = "hls_reserved"
    HLS_RESET = "hls_reset"
    HLS_ACTIVATED = "hls_activated"
    HLS_WRITE_UNCONFIRMED = "hls_write_unconfirmed"
    HLS_WRITE_REJECTED = "hls_write_rejected"
    EXPORT_BATCH_CREATED = "export_batch_created"
    EXPORT_BATCH_COMPLETED = "export_batch_completed"


@dataclass(slots=True)
class Meter:
    """Карточка одного счётчика в реестре."""

    serial_number: str
    status: MeterStatus = MeterStatus.NEW
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    current_hls_key_id: UUID | None = None


@dataclass(slots=True)
class HlsKey:
    """Учётная запись HLS-пароля без хранения открытого значения."""

    id: UUID
    meter_serial_number: str
    status: HlsKeyStatus
    fingerprint: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(slots=True)
class Operation:
    """Неизменяемая запись журнала действий с ПУ."""

    meter_serial_number: str
    operation_type: OperationType
    description: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(slots=True)
class ExportBatch:
    """Неизменяемый состав одной выгрузки HLS."""

    id: UUID
    number: str
    status: ExportBatchStatus
    created_at: datetime
    created_by: str
    row_count: int
    file_name: str | None = None
    file_sha256: str | None = None
    completed_at: datetime | None = None


@dataclass(slots=True)
class ExportBatchItem:
    """Строка экспортной партии без открытого HLS."""

    batch_id: UUID
    meter_id: UUID
    hls_key_id: UUID
    serial_number: str
    personalized_at: datetime    