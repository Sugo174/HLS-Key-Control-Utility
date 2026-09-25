"""Главное окно HLS Key Control Utility."""

import sys
import time

from datetime import UTC

from PySide6.QtCore import (
    QObject,
    QSettings,
    QThread,
    QTimer,
    Qt,
    Signal,
)

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QDialog,
)

from hls_key_control.domain.models import (
    Meter,
    MeterStatus,
)

from hls_key_control.infrastructure.hls_key_repository import (
    SqlHlsKeyRepository,
)
from hls_key_control.infrastructure.meter_repository import (
    SqlMeterRepository,
)
from hls_key_control.infrastructure.operation_journal_repository import (
    SqlOperationJournal,
)

from hls_key_control.config import load_meter_connection_settings
from hls_key_control.services.serial_ports import (
    list_available_serial_ports,
)

from hls_key_control.services.dlms_serial_reader import (
    read_meter_serial_number,
)

from hls_key_control.services.hls_personalization import (
    personalize_meter_hls,
)

from hls_key_control.services.hls_factory_reset import (
    reset_meter_to_factory_hls,
)

from hls_key_control.services.hls_reset_recovery import (
    verify_hls_state_after_reset_failure,
)

from hls_key_control.export_preparation_dialog import (
    ExportPreparationDialog,
)

from hls_key_control.services.excel_export import (
    create_excel_export_from_preview,
)

from hls_key_control.services.export_preparation import (
    prepare_export_preview,
)

TEST_SERIAL_NUMBER = "HLS-SQL-TEST-001"

class MeterReadWorker(QObject):
    """Выполняет физическое чтение ПУ вне потока интерфейса."""

    progress = Signal(str)
    succeeded = Signal(str, float)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, port: str) -> None:
        super().__init__()
        self._port = port

    def run(self) -> None:
        """Считывает серийный номер с безопасными повторами."""
        started_at = time.monotonic()
        last_error: Exception | None = None

        try:
            for attempt in range(1, 4):
                try:
                    self.progress.emit(
                        f"Считывание номера счётчика: попытка {attempt} из 3..."
                    )
                    serial_number = read_meter_serial_number(
                        self._port,
                        self.progress.emit,
                    )
                except Exception as error:
                    last_error = error

                    if attempt < 3:
                        self.progress.emit(
                            "Счётчик временно не ответил. "
                            "Повторяем считывание через 5 секунд..."
                        )
                        time.sleep(5.0)
                        continue

                    raise RuntimeError(
                        "Не удалось считать номер счётчика за 3 попытки."
                    ) from error

                elapsed_seconds = time.monotonic() - started_at
                self.succeeded.emit(
                    serial_number,
                    elapsed_seconds,
                )
                return
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


class HlsPersonalizationWorker(QObject):
    """Выполняет запись и проверку HLS вне потока интерфейса."""

    progress = Signal(str)
    succeeded = Signal(str, float)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        meter: Meter,
        port: str,
    ) -> None:
        super().__init__()
        self._meter = meter
        self._port = port

    def run(self) -> None:
        """Записывает HLS и подтверждает его входом в ПУ."""
        started_at = time.monotonic()

        try:
            serial_number = personalize_meter_hls(
                meter=self._meter,
                port=self._port,
                progress=self.progress.emit,
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            elapsed_seconds = time.monotonic() - started_at
            self.succeeded.emit(
                serial_number,
                elapsed_seconds,
            )
        finally:
            self.finished.emit()


class HlsFactoryResetWorker(QObject):
    """Выполняет физический сброс HLS вне потока интерфейса."""

    succeeded = Signal(str, float)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, port: str) -> None:
        super().__init__()
        self._port = port

    def run(self) -> None:
        started_at = time.monotonic()

        try:
            serial_number = reset_meter_to_factory_hls(
                port=self._port,
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            elapsed_seconds = time.monotonic() - started_at
            self.succeeded.emit(
                serial_number,
                elapsed_seconds,
            )
        finally:
            self.finished.emit()


class HlsResetRecoveryWorker(QObject):
    """Проверяет HLS после неподтверждённого сброса."""

    progress = Signal(str)
    succeeded = Signal(object, float)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        meter: Meter,
        port: str,
    ) -> None:
        super().__init__()
        self._meter = meter
        self._port = port

    def run(self) -> None:
        started_at = time.monotonic()

        try:
            result = verify_hls_state_after_reset_failure(
                meter=self._meter,
                port=self._port,
                progress=self.progress.emit,
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            elapsed_seconds = time.monotonic() - started_at
            self.succeeded.emit(
                result,
                elapsed_seconds,
            )
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    """Главное рабочее окно оператора."""

    def __init__(self) -> None:
        super().__init__()

        self.registry = SqlMeterRepository()
        self.hls_registry = SqlHlsKeyRepository()
        self.current_meter: Meter | None = None
        self.operation_journal = SqlOperationJournal()
        self._read_thread: QThread | None = None
        self._read_worker: MeterReadWorker | None = None
        self._hls_thread: QThread | None = None
        self._hls_worker: HlsPersonalizationWorker | None = None
        self._reset_thread: QThread | None = None
        self._reset_worker: HlsFactoryResetWorker | None = None
        self._recovery_thread: QThread | None = None
        self._recovery_worker: HlsResetRecoveryWorker | None = None
        self._operation_started_at: float | None = None
        self._operation_title = ""
        self._operation_indicator_on = True
        self._operation_cursor_active = False

        self._operation_timer = QTimer(self)
        self._operation_timer.setInterval(500)
        self._operation_timer.timeout.connect(
            self._update_operation_indicator
        )

        self.setWindowTitle("HLS Key Control Utility")
        self.setMinimumSize(860, 560)

        self._settings = QSettings(
            "HlsKeyControl",
            "HlsKeyControl",
        )

        self._build_interface()
        self._refresh_operations()
        self._refresh_meter_ports()

    def _build_interface(self) -> None:
        """Собирает элементы главного окна."""

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(28, 24, 28, 28)
        root_layout.setSpacing(18)

        header_layout = QHBoxLayout()

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)

        title = QLabel("HLS Key Control Utility")
        title.setObjectName("title")

        subtitle = QLabel(
            "Персонализация и контроль HLS-паролей счётчиков"
        )
        subtitle.setObjectName("subtitle")

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)

        connection_status = QLabel("● Режим персонализации")
        connection_status.setObjectName("connectionStatus")

        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        header_layout.addWidget(
            connection_status,
            alignment=Qt.AlignmentFlag.AlignTop,
        )

        meter_card = QFrame()
        meter_card.setObjectName("card")

        meter_layout = QVBoxLayout(meter_card)
        meter_layout.setContentsMargins(22, 20, 22, 20)
        meter_layout.setSpacing(12)

        card_title = QLabel("Подключённый счётчик")
        card_title.setObjectName("cardTitle")

        self.serial_label = QLabel("Серийный номер: —")
        self.serial_label.setObjectName("serialNumber")

        self.meter_status_label = QLabel(
            "Нажмите «Считать номер» для проверки сценария."
        )
        self.meter_status_label.setObjectName("meterStatus")
        self.meter_status_label.setWordWrap(True)

        meter_layout.addWidget(card_title)
        meter_layout.addWidget(self.serial_label)
        meter_layout.addWidget(self.meter_status_label)

        self.operation_panel = QFrame()
        self.operation_panel.setObjectName("operationPanel")
        self.operation_panel.setMinimumHeight(62)

        operation_layout = QHBoxLayout(self.operation_panel)
        operation_layout.setContentsMargins(12, 9, 12, 9)
        operation_layout.setSpacing(12)

        operation_text_layout = QVBoxLayout()
        operation_text_layout.setContentsMargins(0, 0, 0, 0)
        operation_text_layout.setSpacing(2)

        self.operation_title_label = QLabel()
        self.operation_title_label.setObjectName("operationTitle")
        self.operation_title_label.setMinimumHeight(18)

        self.operation_stage_label = QLabel()
        self.operation_stage_label.setObjectName("operationStage")
        self.operation_stage_label.setWordWrap(True)
        self.operation_stage_label.setMinimumHeight(18)

        self.operation_elapsed_label = QLabel("00:00")
        self.operation_elapsed_label.setObjectName("operationElapsed")
        self.operation_elapsed_label.setAlignment(
            Qt.AlignmentFlag.AlignTop
            | Qt.AlignmentFlag.AlignRight
        )

        operation_text_layout.addWidget(self.operation_title_label)
        operation_text_layout.addWidget(self.operation_stage_label)

        operation_layout.addLayout(
            operation_text_layout,
            stretch=1,
        )
        operation_layout.addWidget(self.operation_elapsed_label)

        self.operation_panel.hide()

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)

        self.read_button = QPushButton("Считать номер")
        self.read_button.setObjectName("readButton")
        self.read_button.clicked.connect(self._read_meter_number)

        self.write_button = QPushButton("Записать новый HLS")
        self.write_button.setObjectName("primaryButton")
        self.write_button.setToolTip(
            "Создаёт новый HLS, записывает его в ПУ и проверяет "
            "вход с новым паролем."
        )
        self.write_button.setEnabled(False)
        self.write_button.clicked.connect(
            self._start_hls_personalization
        )

        self.reset_button = QPushButton("Сбросить HLS")
        self.reset_button.setObjectName("secondaryButton")
        self.reset_button.setEnabled(False)
        self.reset_button.setToolTip(
            "Возвращает счётчик к заводскому HLS, проверяет "
            "вход и отзывает активный HLS в реестре."
        )
        self.reset_button.clicked.connect(
            self._start_factory_hls_reset
        )

        self.recovery_button = QPushButton(
            "Проверить HLS"
        )
        self.recovery_button.setObjectName("secondaryButton")
        self.recovery_button.setEnabled(False)
        self.recovery_button.setToolTip(
            "Без записи в счётчик проверяет, действует ли "
            "заводской или персональный HLS после сбоя."
        )
        self.recovery_button.clicked.connect(
            self._start_hls_state_recovery
        )

        self.export_button = QPushButton(
            "Подготовить выгрузку"
        )
        self.export_button.setObjectName("secondaryButton")
        self.export_button.setToolTip(
            "Показывает подтверждённые HLS, которые ещё "
            "не были включены в Excel-выгрузку."
        )
        self.export_button.clicked.connect(
            self._open_export_preparation
        )

        buttons_layout.addWidget(self.read_button)
        buttons_layout.addWidget(self.write_button)
        buttons_layout.addWidget(self.reset_button)
        buttons_layout.addWidget(self.recovery_button)
        buttons_layout.addWidget(self.export_button)
        buttons_layout.addStretch()

        self.system_status_label = QLabel(
            "Реестр: SQL Server · Готово к персонализации"
        )
        self.system_status_label.setObjectName("systemStatus")

        root_layout.addLayout(header_layout)
        root_layout.addWidget(meter_card)
        root_layout.addWidget(self.operation_panel)
        port_card = QFrame()
        port_card.setObjectName("card")

        port_layout = QVBoxLayout(port_card)
        port_layout.setContentsMargins(22, 16, 22, 16)
        port_layout.setSpacing(8)

        port_title = QLabel("Оптическая головка")
        port_title.setObjectName("cardTitle")

        port_controls = QHBoxLayout()
        port_controls.setSpacing(10)

        self.meter_port_combo = QComboBox()
        self.meter_port_combo.setObjectName("meterPortCombo")
        self.meter_port_combo.currentIndexChanged.connect(
            self._save_selected_meter_port
        )

        refresh_ports_button = QPushButton("Обновить порты")
        refresh_ports_button.setObjectName("secondaryButton")
        refresh_ports_button.clicked.connect(
            self._refresh_meter_ports
        )

        port_controls.addWidget(self.meter_port_combo, stretch=1)
        port_controls.addWidget(refresh_ports_button)

        self.meter_port_status_label = QLabel()
        self.meter_port_status_label.setObjectName("meterStatus")

        port_layout.addWidget(port_title)
        port_layout.addLayout(port_controls)
        port_layout.addWidget(self.meter_port_status_label)

        root_layout.addWidget(port_card)
        root_layout.addLayout(buttons_layout)

        journal_card = QFrame()
        journal_card.setObjectName("card")

        journal_layout = QVBoxLayout(journal_card)
        journal_layout.setContentsMargins(22, 18, 22, 18)
        journal_layout.setSpacing(10)

        journal_title = QLabel("Последние операции")
        journal_title.setObjectName("cardTitle")

        self.journal_empty_label = QLabel("Операций пока нет.")
        self.journal_empty_label.setObjectName("meterStatus")

        self.operations_list = QListWidget()
        self.operations_list.setObjectName("operationsList")
        self.operations_list.setMaximumHeight(150)
        self.operations_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.operations_list.hide()

        journal_layout.addWidget(journal_title)
        journal_layout.addWidget(self.journal_empty_label)
        journal_layout.addWidget(self.operations_list)

        root_layout.addWidget(journal_card)
        root_layout.addStretch()
        root_layout.addWidget(self.system_status_label)

        container = QWidget()
        container.setObjectName("rootContainer")
        container.setLayout(root_layout)
        self.setCentralWidget(container)

    def _begin_operation(
        self,
        title: str,
        stage: str,
    ) -> None:
        """Показывает оператору выполнение фоновой операции."""
        self._operation_started_at = time.monotonic()
        self._operation_title = title
        self._operation_indicator_on = True

        self.operation_stage_label.setText(stage)
        self.operation_panel.show()
        self._operation_timer.start()
        self._update_operation_indicator()

        self.meter_status_label.setText(
            f"Выполняется операция: {title}."
        )
        self.meter_status_label.setStyleSheet(
            "color: #61708A;"
        )

        if not self._operation_cursor_active:
            QApplication.setOverrideCursor(
                Qt.CursorShape.WaitCursor
            )
            self._operation_cursor_active = True

    def _set_operation_stage(self, stage: str) -> None:
        """Меняет отображаемый этап текущей операции."""
        if self._operation_started_at is not None:
            self.operation_stage_label.setText(stage)

    def _update_operation_indicator(self) -> None:
        """Обновляет индикатор и длительность операции."""
        if self._operation_started_at is None:
            return

        self._operation_indicator_on = (
            not self._operation_indicator_on
        )
        marker = "●" if self._operation_indicator_on else "○"

        self.operation_title_label.setText(
            f"{marker} {self._operation_title}"
        )

        elapsed = int(
            time.monotonic() - self._operation_started_at
        )
        self.operation_elapsed_label.setText(
            f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        )

    def _finish_operation(self) -> None:
        """Скрывает панель выполнения и возвращает обычный курсор."""
        self._operation_timer.stop()
        self._operation_started_at = None
        self.operation_panel.hide()

        if self._operation_cursor_active:
            QApplication.restoreOverrideCursor()
            self._operation_cursor_active = False

    def _set_meter_result(
        self,
        text: str,
        state: str,
    ) -> None:
        """Показывает итог операции заметной статусной плашкой."""
        styles = {
            "success": (
                "color: #075B32;"
                "background-color: #E4F7EC;"
                "border-left: 4px solid #168447;"
                "border-radius: 6px;"
                "padding: 8px 10px;"
            ),
            "error": (
                "color: #9F1C14;"
                "background-color: #FDECEA;"
                "border-left: 4px solid #C63228;"
                "border-radius: 6px;"
                "padding: 8px 10px;"
            ),
            "neutral": (
                "color: #42516A;"
                "background-color: #EEF3FA;"
                "border-left: 4px solid #71809A;"
                "border-radius: 6px;"
                "padding: 8px 10px;"
            ),
        }

        prefixes = {
            "success": "✓",
            "error": "⚠",
            "neutral": "●",
        }

        self.meter_status_label.setText(
            f"{prefixes[state]} {text}"
        )
        self.meter_status_label.setStyleSheet(styles[state])

    def _refresh_meter_ports(self) -> None:
        """Обновляет список доступных COM-портов."""
        selected_port = self.meter_port_combo.currentData()

        if selected_port is None:
            selected_port = str(
                self._settings.value("meter_port", "")
            )

        if not selected_port:
            try:
                selected_port = (
                    load_meter_connection_settings().port
                )
            except RuntimeError:
                selected_port = ""

        ports = list_available_serial_ports()

        self.meter_port_combo.blockSignals(True)
        self.meter_port_combo.clear()

        for port in ports:
            self.meter_port_combo.addItem(
                f"{port.device} — {port.description}",
                port.device,
            )

        selected_index = self.meter_port_combo.findData(
            selected_port
        )

        if selected_index >= 0:
            self.meter_port_combo.setCurrentIndex(selected_index)
            self.meter_port_status_label.setText(
                f"Выбрана оптическая головка: {selected_port}."
            )
        else:
            self.meter_port_status_label.setText(
                "Выберите COM-порт оптической головки."
            )

        self.meter_port_combo.blockSignals(False)

    def _save_selected_meter_port(self) -> None:
        """Сохраняет выбор COM-порта для следующего запуска."""
        selected_port = self.meter_port_combo.currentData()

        if selected_port is None:
            return

        self._settings.setValue("meter_port", selected_port)
        self.meter_port_status_label.setText(
            f"Выбрана оптическая головка: {selected_port}."
        )

    def _refresh_operations(self) -> None:
        """Обновляет список последних операций из SQL Server."""
        self.operations_list.clear()

        try:
            operations = self.operation_journal.recent()
        except Exception:
            self.operations_list.hide()
            self.journal_empty_label.setText(
                "Не удалось загрузить журнал операций."
            )
            self.journal_empty_label.show()
            return

        if not operations:
            self.operations_list.hide()
            self.journal_empty_label.setText("Операций пока нет.")
            self.journal_empty_label.show()
            return

        self.journal_empty_label.hide()
        self.operations_list.show()

        for operation in operations:
            created_at = operation.created_at

            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)

            time_text = created_at.astimezone().strftime("%H:%M:%S")

            item = QListWidgetItem(
                f"{time_text} · {operation.meter_serial_number} · "
                f"{operation.description}"
            )
            self.operations_list.addItem(item)

    def _open_export_preparation(self) -> None:
        """Открывает просмотр HLS, готовых к новой выгрузке."""
        if (
            self._read_thread is not None
            or self._hls_thread is not None
            or self._reset_thread is not None
            or self._recovery_thread is not None
        ):
            return

        try:
            preview_items = prepare_export_preview()
        except Exception as error:
            QMessageBox.critical(
                self,
                "Не удалось подготовить выгрузку",
                str(error),
            )
            return

        if not preview_items:
            QMessageBox.information(
                self,
                "Нет данных для выгрузки",
                "Нет подтверждённых HLS, которые ещё не были "
                "включены в экспортную партию.",
            )
            return

        dialog = ExportPreparationDialog(
            preview_items,
            self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.export_button.setEnabled(False)
        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor
        )

        try:
            batch, file_path = create_excel_export_from_preview(
                preview_items=preview_items,
                customer_name=dialog.customer_name,
                order_number=dialog.order_number,
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Выгрузка не создана",
                str(error),
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Ошибка создания Excel-файла",
                str(error),
            )
        else:
            self._refresh_operations()
            self.system_status_label.setText(
                "Реестр: SQL Server · Excel-выгрузка создана"
            )

            QMessageBox.information(
                self,
                "Excel-файл создан",
                f"Партия {batch.number} сформирована.\n\n"
                f"Строк: {batch.row_count}\n"
                f"Файл: {file_path}",
            )
        finally:
            QApplication.restoreOverrideCursor()
            self.export_button.setEnabled(True)

    def _read_meter_number(self) -> None:
        """Запускает чтение номера ПУ в фоновом потоке."""
        selected_port = str(
            self.meter_port_combo.currentData() or ""
        )

        if not selected_port:
            self.meter_status_label.setText(
                "Выберите COM-порт оптической головки."
            )
            return

        if (
            self._read_thread is not None
            or self._hls_thread is not None
            or self._reset_thread is not None
            or self._recovery_thread is not None            
        ):
            return

        self.read_button.setEnabled(False)
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self._begin_operation(
            "Чтение счётчика",
            f"Подключение к оптической головке {selected_port}...",
        )
        self.meter_status_label.setText(
            f"Считывание номера счётчика через {selected_port}..."
        )

        thread = QThread(self)
        worker = MeterReadWorker(selected_port)
        worker.moveToThread(thread)
        worker.progress.connect(self._show_read_progress)

        thread.started.connect(worker.run)
        worker.succeeded.connect(self._apply_read_serial_number)
        worker.failed.connect(self._handle_read_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._finish_meter_read)

        self._read_thread = thread
        self._read_worker = worker
        thread.start()

    def _show_read_progress(self, message: str) -> None:
        """Показывает текущий этап чтения."""
        self._set_operation_stage(message)

    def _apply_read_serial_number(
        self,
        serial_number: str,
        elapsed_seconds: float,
    ) -> None:
        """Регистрирует успешно считанный счётчик и обновляет окно."""
        meter, was_created = self.registry.get_or_create(
            serial_number
        )
        self._finish_operation()
        self.current_meter = meter
        self.serial_label.setText(
            f"Серийный номер: {meter.serial_number}"
        )
        self._refresh_operations()

        if meter.status in {
            MeterStatus.NEW,
            MeterStatus.RESET,
        }:
            if was_created:
                text = (
                    f"Счётчик считан за {elapsed_seconds:.1f} с "
                    "и внесён в реестр. Доступно формирование "
                    "нового HLS."
                )
            else:
                text = (
                    f"Счётчик считан за {elapsed_seconds:.1f} с "
                    "и уже есть в реестре. Доступно формирование "
                    "нового HLS."
                )

            self._set_meter_result(text, "success")
            self.system_status_label.setText(
                "Реестр: SQL Server · Счётчик считан"
            )
            self.write_button.setEnabled(True)
            self.reset_button.setEnabled(False)
            self.recovery_button.setEnabled(False)
            return

        if meter.status == MeterStatus.OPERATION_UNCONFIRMED:
            self._set_meter_result(
                "Новый HLS сформирован, но запись в счётчик "
                "не подтверждена. Повторное формирование "
                "заблокировано.",
                "error",
            )
            self.write_button.setEnabled(False)
            self.reset_button.setEnabled(False)
            self.recovery_button.setEnabled(False)
            return

        if meter.status == MeterStatus.PERSONALIZED:
            self._set_meter_result(
                "Новый HLS подтверждён. Доступен физический "
                "сброс до заводского HLS.",
                "success",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · HLS активен"
            )
            self.write_button.setEnabled(False)
            self.reset_button.setEnabled(True)
            self.recovery_button.setEnabled(False)
            return

        if meter.status == MeterStatus.HLS_STATE_UNKNOWN:
            self._set_meter_result(
                "Состояние HLS не подтверждено после сбоя. "
                "Запись и сброс заблокированы до проверки.",
                "error",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · Требуется проверка HLS"
            )
            self.write_button.setEnabled(False)
            self.reset_button.setEnabled(False)
            self.recovery_button.setEnabled(True)
            return

        self._set_meter_result(
            "Текущий статус счётчика не позволяет "
            "записать новый HLS.",
            "neutral",
        )
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(False)

    def _handle_read_failure(self, error_text: str) -> None:
        """Показывает безопасное сообщение об ошибке чтения."""
        self._finish_operation()
        self.current_meter = None
        self.serial_label.setText("Серийный номер: —")
        self._set_meter_result(
            "Не удалось считать номер подключённого ПУ.",
            "error",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Ошибка чтения ПУ"
        )

        QMessageBox.warning(
            self,
            "Не удалось считать номер ПУ",
            error_text,
        )

    def _finish_meter_read(self) -> None:
        """Возвращает интерфейс в рабочее состояние."""
        self._read_thread = None
        self._read_worker = None
        self.read_button.setEnabled(True)
        self.export_button.setEnabled(True)

    def _start_hls_personalization(self) -> None:
        """Запрашивает подтверждение и запускает запись HLS."""
        if (
            self.current_meter is None
            or self._read_thread is not None
            or self._hls_thread is not None
            or self._reset_thread is not None
            or self._recovery_thread is not None
        ):
            return

        selected_port = str(
            self.meter_port_combo.currentData() or ""
        )

        if not selected_port:
            self.meter_status_label.setText(
                "Выберите COM-порт оптической головки."
            )
            return

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Подтверждение записи HLS")
        dialog.setText(
            "Будет создан и записан новый HLS-пароль в ПУ. "
            "Прежний HLS перестанет работать."
        )
        dialog.setInformativeText(
            "Не отключайте оптическую головку и питание ПУ "
            "до завершения проверки нового HLS."
        )

        dialog.addButton(
            "Отмена",
            QMessageBox.ButtonRole.RejectRole,
        )
        confirm_button = dialog.addButton(
            "Записать новый HLS",
            QMessageBox.ButtonRole.AcceptRole,
        )

        dialog.exec()

        if dialog.clickedButton() is not confirm_button:
            return

        try:
            self.hls_registry.reserve_new_key(self.current_meter)
        except ValueError as error:
            self.meter_status_label.setText(str(error))
            self.write_button.setEnabled(False)
            return

        self._refresh_operations()

        self.read_button.setEnabled(False)
        self.write_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.meter_port_combo.setEnabled(False)
        self._begin_operation(
            "Запись нового HLS",
            "Подготовка защищённого сеанса с ПУ...",
        )
        self.meter_status_label.setText(
            "Подготовка записи нового HLS..."
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Выполняется запись HLS"
        )

        thread = QThread(self)
        worker = HlsPersonalizationWorker(
            self.current_meter,
            selected_port,
        )
        worker.moveToThread(thread)
        worker.progress.connect(self._show_hls_progress)

        thread.started.connect(worker.run)
        worker.succeeded.connect(self._apply_hls_success)
        worker.failed.connect(self._handle_hls_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            self._finish_hls_personalization
        )

        self._hls_thread = thread
        self._hls_worker = worker
        thread.start()

    def _show_hls_progress(self, message: str) -> None:
        """Показывает текущий этап записи HLS."""
        self._set_operation_stage(message)

    def _apply_hls_success(
        self,
        serial_number: str,
        elapsed_seconds: float,
    ) -> None:
        """Обновляет окно после подтверждённой записи HLS."""
        self.serial_label.setText(
            f"Серийный номер: {serial_number}"
        )
        self._refresh_operations()
        self._finish_operation()
        self._set_meter_result(
            f"Новый HLS записан и подтверждён за "
            f"{elapsed_seconds:.1f} с.",
            "success",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · HLS активен"
        )

        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)

    def _handle_hls_failure(self, error_text: str) -> None:
        """Блокирует повторную запись после неподтверждённого сбоя."""
        self._refresh_operations()
        self._finish_operation()
        self._set_meter_result(
            "Запись HLS не подтверждена. "
            "Повторная запись заблокирована.",
            "error",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Состояние HLS неизвестно"
        )

        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)

        QMessageBox.warning(
            self,
            "Запись HLS не подтверждена",
            error_text,
        )

    def _finish_hls_personalization(self) -> None:
        """Возвращает интерфейс после фоновой записи HLS."""
        self._hls_thread = None
        self._hls_worker = None
        self.read_button.setEnabled(True)
        self.meter_port_combo.setEnabled(True)
        self.export_button.setEnabled(True)

    def _start_hls_state_recovery(self) -> None:
        """Проверяет HLS после неподтверждённого сброса."""
        if (
            self.current_meter is None
            or self.current_meter.status
            != MeterStatus.HLS_STATE_UNKNOWN
            or self._read_thread is not None
            or self._hls_thread is not None
            or self._reset_thread is not None
            or self._recovery_thread is not None
        ):
            return

        selected_port = str(
            self.meter_port_combo.currentData() or ""
        )

        if not selected_port:
            self._set_meter_result(
                "Выберите COM-порт оптической головки.",
                "error",
            )
            return

        self.read_button.setEnabled(False)
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.meter_port_combo.setEnabled(False)

        self._begin_operation(
            "Проверка состояния HLS",
            "Проверка входа с заводским HLS...",
        )
        self.meter_status_label.setText(
            "Проверка состояния HLS без записи в счётчик..."
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Проверка состояния HLS"
        )

        thread = QThread(self)
        worker = HlsResetRecoveryWorker(
            self.current_meter,
            selected_port,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._show_hls_progress)
        worker.succeeded.connect(
            self._apply_hls_state_recovery
        )
        worker.failed.connect(
            self._handle_hls_state_recovery_failure
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            self._finish_hls_state_recovery
        )

        self._recovery_thread = thread
        self._recovery_worker = worker
        thread.start()

    def _apply_hls_state_recovery(
        self,
        result,
        elapsed_seconds: float,
    ) -> None:
        """Показывает подтверждённое состояние HLS."""
        self._finish_operation()
        self._refresh_operations()
        self.serial_label.setText(
            f"Серийный номер: {result.serial_number}"
        )

        if result.factory_hls_confirmed:
            self._set_meter_result(
                f"Заводской HLS подтверждён за "
                f"{elapsed_seconds:.1f} с. Сброс состоялся.",
                "success",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · HLS сброшен"
            )
            self.write_button.setEnabled(True)
            self.reset_button.setEnabled(False)
        else:
            self._set_meter_result(
                f"Персональный HLS подтверждён за "
                f"{elapsed_seconds:.1f} с. Сброс не состоялся.",
                "success",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · HLS активен"
            )
            self.write_button.setEnabled(False)
            self.reset_button.setEnabled(True)

        self.recovery_button.setEnabled(False)

    def _handle_hls_state_recovery_failure(
        self,
        error_text: str,
    ) -> None:
        """Оставляет счётчик заблокированным при неясном результате."""
        self._finish_operation()
        self._set_meter_result(
            "Не удалось подтвердить состояние HLS. "
            "Повторные записи остаются заблокированными.",
            "error",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Требуется проверка HLS"
        )

        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(True)

        QMessageBox.warning(
            self,
            "Состояние HLS не подтверждено",
            error_text,
        )

    def _finish_hls_state_recovery(self) -> None:
        """Возвращает интерфейс после проверки HLS."""
        self._recovery_thread = None
        self._recovery_worker = None
        self.read_button.setEnabled(True)
        self.meter_port_combo.setEnabled(True)
        self.export_button.setEnabled(True)

    def _start_factory_hls_reset(self) -> None:
        """Запрашивает подтверждение и запускает физический сброс."""
        if (
            self.current_meter is None
            or self.current_meter.status
            != MeterStatus.PERSONALIZED
            or self._read_thread is not None
            or self._hls_thread is not None
            or self._reset_thread is not None
            or self._recovery_thread is not None
        ):
            return

        selected_port = str(
            self.meter_port_combo.currentData() or ""
        )

        if not selected_port:
            self.meter_status_label.setText(
                "Выберите COM-порт оптической головки."
            )
            return

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Подтверждение сброса HLS")
        dialog.setText(
            "ПУ будет возвращён к заводскому HLS. "
            "Текущий персональный HLS перестанет работать."
        )
        dialog.setInformativeText(
            "Будет выполнена запись заводского HLS, затем "
            "проверка входа в ПУ. Не отключайте головку и питание."
        )

        dialog.addButton(
            "Отмена",
            QMessageBox.ButtonRole.RejectRole,
        )
        confirm_button = dialog.addButton(
            "Вернуть заводской HLS",
            QMessageBox.ButtonRole.DestructiveRole,
        )

        dialog.exec()

        if dialog.clickedButton() is not confirm_button:
            return

        self.read_button.setEnabled(False)
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.meter_port_combo.setEnabled(False)
        self._begin_operation(
            "Сброс HLS",
            "Подготовка возврата к заводскому HLS...",
        )
        self.meter_status_label.setText(
            "Возврат ПУ к заводскому HLS..."
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Выполняется сброс HLS"
        )

        thread = QThread(self)
        worker = HlsFactoryResetWorker(selected_port)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.succeeded.connect(
            self._apply_factory_reset_success
        )
        worker.failed.connect(
            self._handle_factory_reset_failure
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._finish_factory_reset)

        self._reset_thread = thread
        self._reset_worker = worker
        thread.start()

    def _apply_factory_reset_success(
        self,
        serial_number: str,
        elapsed_seconds: float,
    ) -> None:
        """Обновляет окно после подтверждённого сброса HLS."""
        self.serial_label.setText(
            f"Серийный номер: {serial_number}"
        )
        self._refresh_operations()
        self._finish_operation()
        self._set_meter_result(
            f"Заводской HLS восстановлен и подтверждён за "
            f"{elapsed_seconds:.1f} с. Доступна новая запись HLS.",
            "success",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · HLS сброшен"
        )

        self.write_button.setEnabled(True)
        self.reset_button.setEnabled(False)

    def _handle_factory_reset_failure(
        self,
        error_text: str,
    ) -> None:
        """Блокирует действия после неподтверждённого сброса."""
        recovery_available = False

        if self.current_meter is not None:
            try:
                self.hls_registry.mark_reset_unconfirmed(
                    self.current_meter
                )
            except Exception as registry_error:
                error_text = (
                    f"{error_text}\n\n"
                    "Не удалось зафиксировать состояние в реестре: "
                    f"{registry_error}"
                )
            else:
                recovery_available = True

        self._finish_operation()

        if recovery_available:
            self._set_meter_result(
                "Сброс HLS не подтверждён. Доступна проверка "
                "состояния без записи в счётчик.",
                "error",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · Требуется проверка HLS"
            )
        else:
            self._set_meter_result(
                "Сброс HLS не подтверждён. Повторные действия "
                "заблокированы до ручной проверки.",
                "error",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · Требуется ручная проверка"
            )

        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(recovery_available)

        QMessageBox.warning(
            self,
            "Сброс HLS не подтверждён",
            error_text,
        )

    def _finish_factory_reset(self) -> None:
        """Возвращает интерфейс после физического сброса."""
        self._reset_thread = None
        self._reset_worker = None
        self.read_button.setEnabled(True)
        self.meter_port_combo.setEnabled(True)
        self.export_button.setEnabled(True)


def apply_application_style(app: QApplication) -> None:
    """Задаёт единый спокойный стиль приложения."""

    app.setStyleSheet(
        """
        QWidget {
            color: #172033;
            font-family: "Segoe UI";
            font-size: 14px;
        }

        QWidget#rootContainer {
            background-color: #F6F8FB;
        }

        QLabel {
            background-color: transparent;
        }

        QLabel#title {
            font-size: 24px;
            font-weight: 700;
        }

        QLabel#subtitle,
        QLabel#meterStatus,
        QLabel#systemStatus {
            color: #61708A;
        }

        QLabel#connectionStatus {
            color: #536076;
            background-color: #E9EDF3;
            border-radius: 8px;
            padding: 8px 12px;
        }

        QFrame#operationPanel {
            background-color: #EEF4FF;
            border: 1px solid #C9D8FF;
            border-radius: 8px;
        }

        QLabel#operationTitle {
            color: #1E4FC4;
            font-weight: 700;
        }

        QLabel#operationStage {
            color: #42516A;
        }

        QLabel#operationElapsed {
            color: #536076;
            font-family: "Consolas";
            font-weight: 600;
        }        

        QFrame#card {
            background-color: white;
            border: 1px solid #DCE3EE;
            border-radius: 12px;
        }

        QLabel#cardTitle {
            font-size: 16px;
            font-weight: 700;
        }

        QLabel#serialNumber {
            font-size: 20px;
            font-weight: 600;
        }

        QPushButton {
            min-height: 38px;
            padding: 0 16px;
            border-radius: 8px;
            font-weight: 600;
        }

        QPushButton#readButton {
            color: #1E4FC4;
            background-color: #EAF0FF;
            border: 1px solid #C9D8FF;
        }

        QPushButton#primaryButton {
            color: white;
            background-color: #2864D7;
            border: 1px solid #2864D7;
        }

        QPushButton#secondaryButton {
            color: #34435C;
            background-color: white;
            border: 1px solid #CCD5E2;
        }

        QPushButton#readButton:disabled,
        QPushButton#primaryButton:disabled,
        QPushButton#secondaryButton:disabled {
            color: #9AA6B8;
            background-color: #E9EDF3;
            border: 1px solid #E9EDF3;
        }

        QListWidget#operationsList {
            background-color: white;
            border: 1px solid #E2E8F1;
            border-radius: 8px;
            padding: 4px;
        }

        QListWidget#operationsList::item {
            padding: 7px;
            border-bottom: 1px solid #EEF2F7;
        }
        """
    )


def main() -> None:
    """Запускает приложение."""

    app = QApplication(sys.argv)
    app.setApplicationDisplayName("HLS Key Control Utility")

    apply_application_style(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()