"""Модели предметной области HLS Key Control Utility."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID


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