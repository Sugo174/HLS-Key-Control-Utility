"""Подготовка подтверждённых HLS к просмотру оператором."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from hls_key_control.infrastructure.export_batch_repository import (
    SqlExportBatchRepository,
)
from hls_key_control.services.hls_protection import HlsProtector


@dataclass(slots=True)
class ExportPreviewItem:
    """Одна строка окна подготовки экспортной партии."""

    meter_id: UUID
    hls_key_id: UUID
    serial_number: str
    hls_password: str
    personalized_at: datetime


def prepare_export_preview() -> list[ExportPreviewItem]:
    """Возвращает доступные HLS для просмотра без изменения БД."""
    repository = SqlExportBatchRepository()
    protector = HlsProtector()
    candidates = repository.list_export_candidates()

    result: list[ExportPreviewItem] = []

    for candidate, encrypted_hls in candidates:
        result.append(
            ExportPreviewItem(
                meter_id=candidate.meter_id,
                hls_key_id=candidate.hls_key_id,
                serial_number=candidate.serial_number,
                hls_password=protector.decrypt(encrypted_hls),
                personalized_at=candidate.personalized_at,
            )
        )

    return result