"""Создание Excel-файлов подтверждённых HLS-партий."""

import hashlib
import re
import socket

from datetime import UTC, datetime
from getpass import getuser
from pathlib import Path
import xlsxwriter
from hls_key_control.domain.models import ExportBatch

from hls_key_control.infrastructure.export_batch_repository import (
    SqlExportBatchRepository,
)

from hls_key_control.services.hls_protection import HlsProtector

from hls_key_control.services.export_preparation import (
    ExportPreviewItem,
)


def create_excel_export_from_preview(
    preview_items: list[ExportPreviewItem],
    customer_name: str,
    order_number: str,
    output_directory: Path | None = None,
) -> tuple[ExportBatch, Path]:
    """Создаёт Excel из состава, показанного оператору."""
    if output_directory is None:
        output_directory = Path.cwd() / "exports"

    output_directory.mkdir(parents=True, exist_ok=True)

    created_by = f"{getuser()}@{socket.gethostname()}"
    repository = SqlExportBatchRepository()
    batch = repository.create_batch_for_candidates(
        created_by=created_by,
        customer_name=customer_name,
        order_number=order_number,
        hls_key_ids=[
            item.hls_key_id
            for item in preview_items
        ],
    )

    file_name = _build_export_file_name(batch)
    target_path = output_directory / file_name
    temporary_path = output_directory / (
        f"{batch.number}.temporary.xlsx"
    )

    if target_path.exists():
        raise RuntimeError(
            "Файл для этой экспортной партии уже существует."
        )

    protector = HlsProtector()
    items = repository.get_batch_items(batch)

    try:
        workbook = xlsxwriter.Workbook(
            str(temporary_path),
            {
                "strings_to_numbers": False,
                "strings_to_formulas": False,
                "strings_to_urls": False,
            },
        )

        try:
            _write_batch_sheet(workbook, batch)
            _write_meters_sheet(
                workbook,
                batch,
                items,
                protector,
            )
        finally:
            workbook.close()

        temporary_path.replace(target_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise

    file_sha256 = _calculate_file_sha256(target_path)

    try:
        repository.complete_batch(
            batch,
            target_path.name,
            file_sha256,
        )
    except Exception as error:
        raise RuntimeError(
            "Excel-файл создан, но не удалось подтвердить "
            "выгрузку в SQL Server. Не создавайте новую партию."
        ) from error

    return batch, target_path


def create_next_excel_export(
    output_directory: Path | None = None,
) -> tuple[ExportBatch, Path]:
    """Создаёт новую экспортную партию и Excel-файл."""
    if output_directory is None:
        output_directory = Path.cwd() / "exports"

    output_directory.mkdir(parents=True, exist_ok=True)

    created_by = f"{getuser()}@{socket.gethostname()}"
    repository = SqlExportBatchRepository()
    batch = repository.create_next_batch(created_by)

    target_path = output_directory / f"{batch.number}.xlsx"
    temporary_path = output_directory / (
        f"{batch.number}.temporary.xlsx"
    )

    if target_path.exists():
        raise RuntimeError(
            "Файл для новой экспортной партии уже существует."
        )

    protector = HlsProtector()
    items = repository.get_batch_items(batch)

    try:
        workbook = xlsxwriter.Workbook(
            str(temporary_path),
            {
                "strings_to_numbers": False,
                "strings_to_formulas": False,
                "strings_to_urls": False,
            },
        )

        try:
            _write_batch_sheet(workbook, batch)
            _write_meters_sheet(
                workbook,
                batch,
                items,
                protector,
            )
        finally:
            workbook.close()

        temporary_path.replace(target_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise

    file_sha256 = _calculate_file_sha256(target_path)

    try:
        repository.complete_batch(
            batch,
            target_path.name,
            file_sha256,
        )
    except Exception as error:
        raise RuntimeError(
            "Excel-файл создан, но не удалось подтвердить "
            "выгрузку в SQL Server. Не создавайте новую партию."
        ) from error

    return batch, target_path


def _build_export_file_name(batch: ExportBatch) -> str:
    """Формирует безопасное и уникальное имя Excel-файла."""
    def normalize(value: str) -> str:
        result = re.sub(
            r"[^0-9A-Za-zА-Яа-яЁё._-]+",
            "_",
            value.strip(),
        )
        result = result.strip("._")

        if not result:
            raise ValueError(
                "Заказчик или номер заказа не подходят "
                "для имени файла."
            )

        return result

    created_date = _to_local_time(
        batch.created_at
    ).strftime("%Y-%m-%d")

    return (
        f"HLS_{normalize(batch.customer_name)}_"
        f"{normalize(batch.order_number)}_"
        f"{created_date}_{batch.number}.xlsx"
    )


def _write_batch_sheet(
    workbook: xlsxwriter.Workbook,
    batch: ExportBatch,
) -> None:
    worksheet = workbook.add_worksheet("Партия")
    worksheet.hide_gridlines(2)
    worksheet.set_column("A:A", 28)
    worksheet.set_column("B:B", 42)

    title_format = workbook.add_format(
        {
            "bold": True,
            "font_size": 16,
            "font_color": "#172033",
        }
    )
    label_format = workbook.add_format(
        {
            "bold": True,
            "bg_color": "#E9EDF3",
            "border": 1,
        }
    )
    value_format = workbook.add_format(
        {
            "border": 1,
        }
    )
    date_format = workbook.add_format(
        {
            "border": 1,
            "num_format": "dd.mm.yyyy hh:mm:ss",
        }
    )

    created_at = _to_local_time(batch.created_at)

    worksheet.write("A1", "Экспортная партия HLS", title_format)
    worksheet.write("A3", "Номер партии", label_format)
    worksheet.write_string("B3", batch.number, value_format)
    worksheet.write("A4", "Создана", label_format)
    worksheet.write_datetime("B4", created_at, date_format)
    worksheet.write("A5", "Создал", label_format)
    worksheet.write("B5", batch.created_by, value_format)
    worksheet.write("A6", "Заказчик", label_format)
    worksheet.write("B6", batch.customer_name, value_format)
    worksheet.write("A7", "Номер заказа", label_format)
    worksheet.write("B7", batch.order_number, value_format)
    worksheet.write("A8", "Количество счётчиков", label_format)
    worksheet.write_number("B8", batch.row_count, value_format)
    worksheet.write(
        "A10",
        "Файл содержит конфиденциальные HLS-пароли.",
    )


def _write_meters_sheet(
    workbook: xlsxwriter.Workbook,
    batch: ExportBatch,
    items,
    protector: HlsProtector,
) -> None:
    worksheet = workbook.add_worksheet("ПУ")
    worksheet.hide_gridlines(2)
    worksheet.freeze_panes(1, 0)
    worksheet.autofilter(0, 0, len(items), 4)

    worksheet.set_column("A:A", 8)
    worksheet.set_column("B:B", 22)
    worksheet.set_column("C:C", 20)
    worksheet.set_column("D:D", 22)
    worksheet.set_column("E:E", 24)

    header_format = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#2563EB",
            "border": 1,
            "align": "center",
        }
    )
    text_format = workbook.add_format(
        {
            "border": 1,
            "num_format": "@",
        }
    )
    date_format = workbook.add_format(
        {
            "border": 1,
            "num_format": "dd.mm.yyyy hh:mm:ss",
        }
    )

    headers = [
        "№",
        "Номер партии",
        "Серийный номер ПУ",
        "Новый HLS",
        "Дата персонализации",
    ]

    for column, header in enumerate(headers):
        worksheet.write(0, column, header, header_format)

    for row_number, (item, encrypted_hls) in enumerate(
        items,
        start=1,
    ):
        hls_password = protector.decrypt(encrypted_hls)

        worksheet.write_number(
            row_number,
            0,
            row_number,
            text_format,
        )
        worksheet.write_string(
            row_number,
            1,
            batch.number,
            text_format,
        )
        worksheet.write_string(
            row_number,
            2,
            item.serial_number,
            text_format,
        )
        worksheet.write_string(
            row_number,
            3,
            hls_password,
            text_format,
        )
        worksheet.write_datetime(
            row_number,
            4,
            _to_local_time(item.personalized_at),
            date_format,
        )


def _to_local_time(value: datetime) -> datetime:
    """Возвращает время БД в локальной временной зоне."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)

    return value.astimezone().replace(tzinfo=None)


def _calculate_file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


if __name__ == "__main__":
    batch, file_path = create_next_excel_export()

    print(
        f"Создана партия {batch.number}. "
        f"Строк: {batch.row_count}. "
        f"Файл: {file_path}"
    )