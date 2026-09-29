"""Главное окно HLS Key Control Utility."""

import sys
import time

from datetime import UTC
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QSettings,
    QThread,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon, QPixmap

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
    QLineEdit,
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

from hls_key_control.services.hls_service import (
    verify_unconfirmed_service_hls,
    write_service_hls,
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

from hls_key_control.services.application_log import (
    write_application_log,
)

TEST_SERIAL_NUMBER = "HLS-SQL-TEST-001"
ASSETS_DIRECTORY = Path(__file__).resolve().parent / "assets"
APP_ICON_PATH = ASSETS_DIRECTORY / "emis-electra-app.png"
BRAND_LOGO_PATH = ASSETS_DIRECTORY / "emis-electra-logo.png"

class WorkMode(StrEnum):
    """Рабочие режимы HLS Key Control Utility."""

    PERSONALIZATION = "personalization"
    SERVICE = "service"


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
                    write_application_log(
                        "Чтение счётчика: "
                        f"попытка {attempt} из 3 не удалась. "
                        f"Причина: {error}"
                    )

                    if attempt < 3:
                        self.progress.emit(
                            "Счётчик временно не ответил. "
                            "Повторяем считывание через 5 секунд..."
                        )
                        time.sleep(5.0)
                        continue

                    raise RuntimeError(
                        "Не удалось считать номер счётчика за 3 попытки. "
                        f"Последняя причина: {error}"
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


class HlsServiceWriteWorker(QObject):
    """Записывает сервисный HLS вне потока интерфейса."""

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
        """Записывает и подтверждает сервисный HLS."""
        started_at = time.monotonic()

        try:
            serial_number = write_service_hls(
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


class HlsServiceRecoveryWorker(QObject):
    """Проверяет неподтверждённый сервисный HLS вне потока интерфейса."""

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
        """Проверяет сервисный HLS без повторной записи."""
        started_at = time.monotonic()

        try:
            serial_number = verify_unconfirmed_service_hls(
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
        self._work_mode = WorkMode.PERSONALIZATION
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
        self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setMinimumSize(860, 680)

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

        brand_logo = QLabel()
        brand_logo.setObjectName("brandLogo")
        brand_logo.setPixmap(
            QPixmap(str(BRAND_LOGO_PATH)).scaledToWidth(
                132,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        brand_logo.setFixedSize(132, 42)
        brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

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

        header_layout.addWidget(brand_logo)
        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        mode_switch = QFrame()
        mode_switch.setObjectName("modeSwitch")

        mode_layout = QVBoxLayout(mode_switch)
        mode_layout.setContentsMargins(10, 8, 10, 8)
        mode_layout.setSpacing(6)

        mode_selector = QFrame()
        mode_selector.setObjectName("modeSelector")

        mode_buttons_layout = QHBoxLayout(mode_selector)
        mode_buttons_layout.setContentsMargins(3, 3, 3, 3)
        mode_buttons_layout.setSpacing(2)

        self.personalization_mode_button = QPushButton(
            "Персонализация"
        )
        self.personalization_mode_button.setObjectName("modeButton")
        self.personalization_mode_button.setCheckable(True)
        self.personalization_mode_button.setChecked(True)

        self.service_mode_button = QPushButton("Обслуживание")
        self.service_mode_button.setObjectName("modeButton")
        self.service_mode_button.setCheckable(True)
        self.mode_indicator_label = QLabel(
            "● Режим персонализации"
        )
        self.mode_indicator_label.setObjectName("modeIndicator")
        mode_buttons_layout.addWidget(
            self.personalization_mode_button
        )
        mode_buttons_layout.addWidget(self.service_mode_button)

        mode_layout.addWidget(mode_selector)
        mode_layout.addWidget(self.mode_indicator_label)

        self.personalization_mode_button.clicked.connect(
            lambda: self._set_work_mode(
                WorkMode.PERSONALIZATION
            )
        )
        self.service_mode_button.clicked.connect(
            lambda: self._set_work_mode(WorkMode.SERVICE)
        )

        header_layout.addWidget(mode_switch)

        meter_card = QFrame()
        meter_card.setObjectName("card")

        meter_layout = QVBoxLayout(meter_card)
        meter_layout.setContentsMargins(22, 20, 22, 20)
        meter_layout.setSpacing(12)

        card_title = QLabel("Подключённый счётчик")
        card_title.setObjectName("cardTitle")

        self.serial_label = QLabel("Серийный номер: —")
        self.serial_label.setObjectName("serialNumber")
        self.serial_label.setFixedHeight(34)
        self.serial_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        self.meter_status_label = QLabel(
            "Нажмите «Считать номер» для проверки сценария."
        )
        self.meter_status_label.setObjectName("meterStatus")
        self.meter_status_label.setWordWrap(True)
        self.meter_status_label.setMinimumHeight(24)

        meter_layout.addWidget(card_title)
        meter_layout.addWidget(self.serial_label)
        meter_layout.addWidget(self.meter_status_label)

        self.operation_panel = QFrame()
        self.operation_panel.setObjectName("operationPanel")
        self.operation_panel.setMinimumHeight(72)

        operation_layout = QHBoxLayout(self.operation_panel)
        operation_layout.setContentsMargins(12, 9, 12, 9)
        operation_layout.setSpacing(12)

        operation_text_layout = QVBoxLayout()
        operation_text_layout.setContentsMargins(0, 0, 0, 0)
        operation_text_layout.setSpacing(2)

        self.operation_title_label = QLabel()
        self.operation_title_label.setObjectName("operationTitle")
        self.operation_title_label.setMinimumHeight(22)

        self.operation_stage_label = QLabel()
        self.operation_stage_label.setObjectName("operationStage")
        self.operation_stage_label.setWordWrap(True)
        self.operation_stage_label.setMinimumHeight(22)

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
            self._start_hls_write
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
            self._start_hls_recovery
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
            self._open_mode_action
        )

        buttons_layout.addWidget(self.read_button)
        buttons_layout.addWidget(self.write_button)
        buttons_layout.addWidget(self.recovery_button)
        buttons_layout.addWidget(self.reset_button)
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

        self.refresh_ports_button = QPushButton(
            "Обновить порты"
        )
        self.refresh_ports_button.setObjectName(
            "secondaryButton"
        )
        self.refresh_ports_button.clicked.connect(
            self._refresh_meter_ports_from_button
        )

        port_controls.addWidget(self.meter_port_combo, stretch=1)
        port_controls.addWidget(self.refresh_ports_button)

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

    def _set_work_mode(self, mode: WorkMode) -> None:
        """Переключает сценарий работы и очищает прошлый результат."""
        if (
            self._read_thread is not None
            or self._hls_thread is not None
            or self._reset_thread is not None
            or self._recovery_thread is not None
        ):
            return

        self._work_mode = mode

        is_personalization = (
            mode == WorkMode.PERSONALIZATION
        )

        self.personalization_mode_button.setChecked(
            is_personalization
        )
        self.service_mode_button.setChecked(
            not is_personalization
        )

        self.current_meter = None
        self.serial_label.setText("Серийный номер: —")
        self.meter_status_label.setStyleSheet("")

        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(False)

        if is_personalization:
            self.mode_indicator_label.setText(
                "● Режим персонализации"
            )
            self.meter_status_label.setText(
                "Нажмите «Считать номер» для проверки сценария."
            )
            self.write_button.setText("Записать новый HLS")
            self.export_button.setText("Подготовить выгрузку")
            self.export_button.setEnabled(True)
            self.system_status_label.setText(
                "Реестр: SQL Server · Готово к персонализации"
            )
        else:
            self.mode_indicator_label.setText(
                "● Режим обслуживания"
            )
            self.meter_status_label.setText(
                "Считайте номер счётчика для сервисной работы."
            )
            self.write_button.setText("Записать сервисный HLS")
            self.export_button.setText("Прочитать HLS")
            self.export_button.setEnabled(False)
            self.system_status_label.setText(
                "Реестр: SQL Server · Режим обслуживания"
            )

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
        self.meter_status_label.setMinimumHeight(24)
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
        required_height = (
            self.meter_status_label.heightForWidth(
                self.meter_status_label.width()
            )
        )
        self.meter_status_label.setMinimumHeight(
            max(24, required_height)
        )

        serial_number = (
            self.current_meter.serial_number
            if self.current_meter is not None
            else "неизвестный счётчик"
        )
        write_application_log(
            f"{serial_number} · {text}"
        )

    def _refresh_meter_ports_from_button(self) -> None:
        """Обновляет порты с понятной обратной связью."""
        self.refresh_ports_button.setEnabled(False)
        self.refresh_ports_button.setText("Обновляем...")
        QApplication.processEvents()

        try:
            self._refresh_meter_ports()

            selected_port = str(
                self.meter_port_combo.currentData() or ""
            )

            if selected_port:
                message = (
                    "Список COM-портов обновлён.\n"
                    f"Выбрана оптическая головка: {selected_port}."
                )
            else:
                message = (
                    "Список COM-портов обновлён.\n"
                    "Выберите оптическую головку."
                )

            self.meter_port_status_label.setText(
                message.replace("\n", " ")
            )

            dialog = QDialog(self)
            dialog.setObjectName("confirmationDialog")
            dialog.setWindowTitle("Порты обновлены")
            dialog.setMinimumWidth(380)

            dialog_layout = QVBoxLayout(dialog)
            dialog_layout.setContentsMargins(24, 22, 24, 22)
            dialog_layout.setSpacing(14)

            title = QLabel("Порты обновлены")
            title.setObjectName("confirmationTitle")

            description = QLabel(message)
            description.setWordWrap(True)

            buttons_layout = QHBoxLayout()
            buttons_layout.addStretch()

            ok_button = QPushButton("ОК")
            ok_button.setObjectName("primaryButton")
            ok_button.setDefault(True)
            ok_button.clicked.connect(dialog.accept)

            buttons_layout.addWidget(ok_button)

            dialog_layout.addWidget(title)
            dialog_layout.addWidget(description)
            dialog_layout.addLayout(buttons_layout)

            dialog.exec()

        finally:
            self.refresh_ports_button.setText(
                "Обновить порты"
            )
            self.refresh_ports_button.setEnabled(True)


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

    def _open_mode_action(self) -> None:
        """Выполняет действие последней кнопки текущего режима."""
        if self._work_mode == WorkMode.SERVICE:
            self._show_service_hls()
            return

        self._open_export_preparation()

    def _show_service_hls(self) -> None:
        """Показывает только подтверждённый сервисный HLS."""
        if self.current_meter is None:
            return

        try:
            service_hls = (
                self.hls_registry.get_active_service_hls_password(
                    self.current_meter
                )
            )
        except ValueError:
            dialog = QDialog(self)
            dialog.setObjectName("confirmationDialog")
            dialog.setWindowTitle("Сервисный HLS не найден")
            dialog.setMinimumWidth(500)

            dialog_layout = QVBoxLayout(dialog)
            dialog_layout.setContentsMargins(24, 22, 24, 22)
            dialog_layout.setSpacing(14)

            title = QLabel("Текущий HLS нельзя прочитать")
            title.setObjectName("confirmationTitle")

            description = QLabel(
                "Счётчик не позволяет прочитать действующий "
                "HLS-пароль. В реестре нет подтверждённого "
                "сервисного HLS, созданного этим приложением.\n\n"
                "Запишите новый сервисный HLS или сбросьте "
                "счётчик до заводского HLS."
            )
            description.setWordWrap(True)

            buttons_layout = QHBoxLayout()
            buttons_layout.addStretch()

            ok_button = QPushButton("Понятно")
            ok_button.setObjectName("primaryButton")
            ok_button.setDefault(True)
            ok_button.clicked.connect(dialog.accept)

            buttons_layout.addWidget(ok_button)

            dialog_layout.addWidget(title)
            dialog_layout.addWidget(description)
            dialog_layout.addLayout(buttons_layout)

            dialog.exec()
            return

        dialog = QDialog(self)
        dialog.setObjectName("confirmationDialog")
        dialog.setWindowTitle("Сервисный HLS")
        dialog.setMinimumWidth(500)

        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(24, 22, 24, 22)
        dialog_layout.setSpacing(14)

        title = QLabel("Подтверждённый сервисный HLS")
        title.setObjectName("confirmationTitle")

        description = QLabel(
            "Этот HLS был создан приложением в режиме "
            "обслуживания. Он не включается в Excel-выгрузку."
        )
        description.setWordWrap(True)

        password_field = QLineEdit(service_hls)
        password_field.setReadOnly(True)
        password_field.setContextMenuPolicy(
            Qt.ContextMenuPolicy.NoContextMenu
        )
        password_field.selectAll()

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        copy_button = QPushButton("Скопировать")
        copy_button.setObjectName("secondaryButton")

        def copy_service_hls() -> None:
            QApplication.clipboard().setText(service_hls)
            copy_button.setText("Скопировано")

        copy_button.clicked.connect(copy_service_hls)

        close_button = QPushButton("Закрыть")
        close_button.setObjectName("primaryButton")
        close_button.setDefault(True)
        close_button.clicked.connect(dialog.accept)

        buttons_layout.addWidget(copy_button)
        buttons_layout.addWidget(close_button)

        dialog_layout.addWidget(title)
        dialog_layout.addWidget(description)
        dialog_layout.addWidget(password_field)
        dialog_layout.addLayout(buttons_layout)

        dialog.exec()

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
            result_dialog = QDialog(self)
            result_dialog.setObjectName("confirmationDialog")
            result_dialog.setWindowTitle("Нет данных для выгрузки")
            result_dialog.setMinimumWidth(460)

            result_layout = QVBoxLayout(result_dialog)
            result_layout.setContentsMargins(24, 22, 24, 22)
            result_layout.setSpacing(14)

            title = QLabel("Нет данных для выгрузки")
            title.setObjectName("confirmationTitle")

            description = QLabel(
                "Нет подтверждённых HLS, которые ещё не были "
                "включены в экспортную партию."
            )
            description.setWordWrap(True)

            buttons_layout = QHBoxLayout()
            buttons_layout.addStretch()

            ok_button = QPushButton("ОК")
            ok_button.setObjectName("primaryButton")
            ok_button.setDefault(True)
            ok_button.clicked.connect(result_dialog.accept)

            buttons_layout.addWidget(ok_button)

            result_layout.addWidget(title)
            result_layout.addWidget(description)
            result_layout.addLayout(buttons_layout)

            result_dialog.exec()
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

        error_title = None
        error_text = None
        batch = None
        file_path = None

        try:
            batch, file_path = create_excel_export_from_preview(
                preview_items=preview_items,
                customer_name=dialog.customer_name,
                order_number=dialog.order_number,
            )
        except ValueError as error:
            error_title = "Выгрузка не создана"
            error_text = str(error)
        except Exception as error:
            error_title = "Ошибка создания Excel-файла"
            error_text = str(error)
        finally:
            QApplication.restoreOverrideCursor()
            self.export_button.setEnabled(True)

        if error_text is not None:
            QMessageBox.warning(
                self,
                error_title,
                error_text,
            )
            return

        self._refresh_operations()
        self.system_status_label.setText(
            "Реестр: SQL Server · Excel-выгрузка создана"
        )

        write_application_log(
            f"Excel-выгрузка создана: партия "
            f"{batch.number}, строк: {batch.row_count}, "
            f"файл: {file_path.name}."
        )

        result_dialog = QDialog(self)
        result_dialog.setObjectName("confirmationDialog")
        result_dialog.setWindowTitle("Excel-файл создан")
        result_dialog.setMinimumWidth(500)

        result_layout = QVBoxLayout(result_dialog)
        result_layout.setContentsMargins(24, 22, 24, 22)
        result_layout.setSpacing(14)

        title = QLabel("Excel-файл экспортирован")
        title.setObjectName("confirmationTitle")

        description = QLabel(
            f"Строк выгружено: {batch.row_count}\n"
            f"Файл:\n{file_path}"
        )
        description.setWordWrap(True)

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        ok_button = QPushButton("ОК")
        ok_button.setObjectName("primaryButton")
        ok_button.setDefault(True)
        ok_button.clicked.connect(result_dialog.accept)

        buttons_layout.addWidget(ok_button)

        result_layout.addWidget(title)
        result_layout.addWidget(description)
        result_layout.addLayout(buttons_layout)

        result_dialog.exec()

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
        if self._work_mode == WorkMode.SERVICE:
            self._apply_service_read_result(
                meter,
                was_created,
                elapsed_seconds,
            )
            return
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
            if self.hls_registry.has_active_service_hls(meter):
                self._set_meter_result(
                    "Сервисный HLS подтверждён. Доступны запись "
                    "персонализационного HLS или сброс до "
                    "заводского HLS.",
                    "success",
                )
                self.system_status_label.setText(
                    "Реестр: SQL Server · Сервисный HLS активен"
                )
                self.write_button.setEnabled(True)
                self.reset_button.setEnabled(True)
                self.recovery_button.setEnabled(False)
                return

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

    def _apply_service_read_result(
        self,
        meter: Meter,
        was_created: bool,
        elapsed_seconds: float,
    ) -> None:
        """Показывает результат чтения в режиме обслуживания."""
        if meter.status == MeterStatus.OPERATION_UNCONFIRMED:
            self._set_meter_result(
                "Есть неподтверждённая операция с HLS. "
                "Сервисные действия пока заблокированы.",
                "error",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · Требуется проверка HLS"
            )
            self.write_button.setEnabled(False)
            self.reset_button.setEnabled(False)
            self.recovery_button.setEnabled(True)
            self.export_button.setEnabled(False)
            return

        if meter.status == MeterStatus.HLS_STATE_UNKNOWN:
            self._set_meter_result(
                "Состояние HLS не подтверждено после сбоя. "
                "Сервисные действия пока заблокированы.",
                "error",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · Требуется проверка HLS"
            )
            return

        if was_created:
            registry_text = "внесён в реестр"
        else:
            registry_text = "найден в реестре"

        can_reset_hls = meter.status in {
            MeterStatus.NEW,
            MeterStatus.PERSONALIZED,
        }

        if meter.status == MeterStatus.NEW:
            action_text = (
                "Текущий HLS неизвестен. Доступны запись "
                "сервисного HLS или возврат к заводскому HLS."
            )
        elif meter.status == MeterStatus.RESET:
            action_text = (
                "Заводской HLS уже подтверждён. Доступна "
                "запись сервисного HLS."
            )
        else:
            action_text = (
                "Доступны замена сервисного HLS и сброс "
                "до заводского HLS."
            )

        self._set_meter_result(
            f"Счётчик считан за {elapsed_seconds:.1f} с и "
            f"{registry_text}. {action_text}",
            "success",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Счётчик считан в сервисном режиме"
        )

        self.write_button.setEnabled(True)
        self.reset_button.setEnabled(can_reset_hls)
        self.recovery_button.setEnabled(False)
        self.export_button.setEnabled(True)

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

        if self._work_mode == WorkMode.PERSONALIZATION:
            self.export_button.setEnabled(True)

    def _start_hls_write(self) -> None:
        """Запускает запись HLS для выбранного рабочего режима."""
        if self._work_mode == WorkMode.SERVICE:
            self._start_service_hls_write()
            return

        self._start_hls_personalization()

    def _start_service_hls_write(self) -> None:
        """Запрашивает подтверждение и записывает сервисный HLS."""
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
            self._set_meter_result(
                "Выберите COM-порт оптической головки.",
                "error",
            )
            return

        dialog = QDialog(self)
        dialog.setObjectName("confirmationDialog")
        dialog.setWindowTitle("Подтверждение сервисной записи HLS")
        dialog.setMinimumWidth(500)

        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(24, 22, 24, 22)
        dialog_layout.setSpacing(14)

        title = QLabel("Записать сервисный HLS?")
        title.setObjectName("confirmationTitle")

        description = QLabel(
            "Будет создан и записан новый сервисный HLS-пароль "
            "в счётчик. Этот HLS не будет включён "
            "в Excel-выгрузку."
        )
        description.setWordWrap(True)

        warning = QLabel(
            "Прежний HLS перестанет работать. Не отключайте "
            "оптическую головку и питание до завершения "
            "проверки нового HLS."
        )
        warning.setObjectName("confirmationWarning")
        warning.setWordWrap(True)

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        cancel_button = QPushButton("Отмена")
        cancel_button.setObjectName("secondaryButton")
        cancel_button.clicked.connect(dialog.reject)

        confirm_button = QPushButton("Записать сервисный HLS")
        confirm_button.setObjectName("primaryButton")
        confirm_button.clicked.connect(dialog.accept)

        buttons_layout.addWidget(cancel_button)
        buttons_layout.addWidget(confirm_button)

        dialog_layout.addWidget(title)
        dialog_layout.addWidget(description)
        dialog_layout.addWidget(warning)
        dialog_layout.addLayout(buttons_layout)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.read_button.setEnabled(False)
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.meter_port_combo.setEnabled(False)
        self.personalization_mode_button.setEnabled(False)
        self.service_mode_button.setEnabled(False)

        self._begin_operation(
            "Запись сервисного HLS",
            "Подготовка защищённого сеанса с ПУ...",
        )
        self.meter_status_label.setText(
            "Подготовка записи сервисного HLS..."
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Выполняется сервисная запись HLS"
        )

        thread = QThread(self)
        worker = HlsServiceWriteWorker(
            self.current_meter,
            selected_port,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._show_hls_progress)
        worker.succeeded.connect(
            self._apply_service_hls_write_success
        )
        worker.failed.connect(
            self._handle_service_hls_write_failure
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            self._finish_service_hls_write
        )

        self._hls_thread = thread
        self._hls_worker = worker
        thread.start()

    def _apply_service_hls_write_success(
        self,
        serial_number: str,
        elapsed_seconds: float,
    ) -> None:
        """Показывает подтверждённую запись сервисного HLS."""
        self._finish_operation()
        self._refresh_operations()
        self.serial_label.setText(
            f"Серийный номер: {serial_number}"
        )
        self._set_meter_result(
            f"Сервисный HLS записан и подтверждён за "
            f"{elapsed_seconds:.1f} с.",
            "success",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Сервисный HLS активен"
        )
        self.export_button.setEnabled(True)

    def _handle_service_hls_write_failure(
        self,
        error_text: str,
    ) -> None:
        """Блокирует повторную сервисную запись при неясном результате."""
        self._finish_operation()
        self._refresh_operations()
        self._set_meter_result(
            "Запись сервисного HLS не подтверждена. "
            "Повторная запись заблокирована.",
            "error",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Состояние HLS неизвестно"
        )

        QMessageBox.warning(
            self,
            "Запись сервисного HLS не подтверждена",
            error_text,
        )

    def _finish_service_hls_write(self) -> None:
        """Возвращает интерфейс после сервисной записи HLS."""
        self._hls_thread = None
        self._hls_worker = None
        self.read_button.setEnabled(True)
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.meter_port_combo.setEnabled(True)
        self.personalization_mode_button.setEnabled(True)
        self.service_mode_button.setEnabled(True)

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

        dialog = QDialog(self)
        dialog.setObjectName("confirmationDialog")
        dialog.setWindowTitle("Подтверждение записи HLS")
        dialog.setMinimumWidth(470)

        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(24, 22, 24, 22)
        dialog_layout.setSpacing(14)

        title = QLabel("Записать новый HLS?")
        title.setObjectName("confirmationTitle")

        if self.hls_registry.has_active_service_hls(
            self.current_meter
        ):
            description_text = (
                "Сервисный HLS будет заменён новым "
                "персонализационным HLS-паролем. После "
                "подтверждения новый ключ станет доступен "
                "для Excel-выгрузки."
            )
        else:
            description_text = (
                "Будет создан и записан новый HLS-пароль "
                "в счётчик. Прежний HLS перестанет работать."
            )

        description = QLabel(description_text)
        description.setWordWrap(True)

        warning = QLabel(
            "Не отключайте оптическую головку и питание "
            "до завершения проверки нового HLS."
        )
        warning.setObjectName("confirmationWarning")
        warning.setWordWrap(True)

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        cancel_button = QPushButton("Отмена")
        cancel_button.setObjectName("secondaryButton")
        cancel_button.clicked.connect(dialog.reject)

        confirm_button = QPushButton("Записать новый HLS")
        confirm_button.setObjectName("primaryButton")
        confirm_button.clicked.connect(dialog.accept)

        buttons_layout.addWidget(cancel_button)
        buttons_layout.addWidget(confirm_button)

        dialog_layout.addWidget(title)
        dialog_layout.addWidget(description)
        dialog_layout.addWidget(warning)
        dialog_layout.addLayout(buttons_layout)

        if dialog.exec() != QDialog.DialogCode.Accepted:
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

    def _start_hls_recovery(self) -> None:
        """Выбирает безопасную проверку для текущего режима."""
        if (
            self._work_mode == WorkMode.SERVICE
            and self.current_meter is not None
            and self.hls_registry.has_unconfirmed_service_hls(
                self.current_meter
            )
        ):
            self._start_service_hls_recovery()
            return

        self._start_hls_state_recovery()

    def _start_service_hls_recovery(self) -> None:
        """Проверяет сервисный HLS после неподтверждённой записи."""
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
        self.personalization_mode_button.setEnabled(False)
        self.service_mode_button.setEnabled(False)

        self._begin_operation(
            "Проверка сервисного HLS",
            "Проверка входа с новым сервисным HLS...",
        )
        self.meter_status_label.setText(
            "Проверка сервисного HLS без записи в счётчик..."
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Проверка сервисного HLS"
        )

        thread = QThread(self)
        worker = HlsServiceRecoveryWorker(
            self.current_meter,
            selected_port,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._show_hls_progress)
        worker.succeeded.connect(
            self._apply_service_hls_recovery_success
        )
        worker.failed.connect(
            self._handle_service_hls_recovery_failure
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            self._finish_service_hls_recovery
        )

        self._recovery_thread = thread
        self._recovery_worker = worker
        thread.start()

    def _apply_service_hls_recovery_success(
        self,
        serial_number: str,
        elapsed_seconds: float,
    ) -> None:
        """Показывает подтверждённый сервисный HLS."""
        self._finish_operation()
        self._refresh_operations()
        self.serial_label.setText(
            f"Серийный номер: {serial_number}"
        )
        self._set_meter_result(
            f"Сервисный HLS подтверждён за "
            f"{elapsed_seconds:.1f} с.",
            "success",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Сервисный HLS активен"
        )

        self.write_button.setEnabled(True)
        self.reset_button.setEnabled(True)
        self.recovery_button.setEnabled(False)
        self.export_button.setEnabled(True)

    def _handle_service_hls_recovery_failure(
        self,
        error_text: str,
    ) -> None:
        """Оставляет сервисный HLS заблокированным при неясном результате."""
        self._finish_operation()
        self._refresh_operations()
        self._set_meter_result(
            "Не удалось подтвердить сервисный HLS. "
            "Повторная запись остаётся заблокированной.",
            "error",
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · Требуется проверка сервисного HLS"
        )

        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.recovery_button.setEnabled(True)
        self.export_button.setEnabled(False)

        QMessageBox.warning(
            self,
            "Сервисный HLS не подтверждён",
            error_text,
        )

    def _finish_service_hls_recovery(self) -> None:
        """Возвращает интерфейс после проверки сервисного HLS."""
        self._recovery_thread = None
        self._recovery_worker = None
        self.read_button.setEnabled(True)
        self.meter_port_combo.setEnabled(True)
        self.personalization_mode_button.setEnabled(True)
        self.service_mode_button.setEnabled(True)

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
                f"{elapsed_seconds:.1f} с. Реестр обновлён.",
                "success",
            )
            self.system_status_label.setText(
                "Реестр: SQL Server · Заводской HLS подтверждён"
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
            or (
                self._work_mode == WorkMode.PERSONALIZATION
                and self.current_meter.status
                != MeterStatus.PERSONALIZED
            )
            or (
                self._work_mode == WorkMode.SERVICE
                and self.current_meter.status not in {
                    MeterStatus.NEW,
                    MeterStatus.PERSONALIZED,
                }
            )
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

        dialog = QDialog(self)
        dialog.setObjectName("confirmationDialog")
        dialog.setWindowTitle("Подтверждение сброса HLS")
        dialog.setMinimumWidth(470)

        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(24, 22, 24, 22)
        dialog_layout.setSpacing(14)

        title = QLabel("Вернуть заводской HLS?")
        title.setObjectName("confirmationTitle")

        if (
            self._work_mode == WorkMode.SERVICE
            and self.current_meter.status == MeterStatus.NEW
        ):
            description_text = (
                "Текущий HLS счётчика неизвестен. Устройство "
                "будет принудительно возвращено к заводскому "
                "HLS с обязательной проверкой входа."
            )
        elif self._work_mode == WorkMode.SERVICE:
            description_text = (
                "Счётчик будет возвращён к заводскому HLS. "
                "Текущий сервисный HLS перестанет работать."
            )
        else:
            description_text = (
                "Счётчик будет возвращён к заводскому HLS. "
                "Текущий персонализационный HLS перестанет "
                "работать."
            )

        description = QLabel(description_text)
        description.setWordWrap(True)

        warning = QLabel(
            "Не отключайте оптическую головку и питание "
            "до завершения проверки."
        )
        warning.setObjectName("confirmationWarning")
        warning.setWordWrap(True)

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        cancel_button = QPushButton("Отмена")
        cancel_button.setObjectName("secondaryButton")
        cancel_button.clicked.connect(dialog.reject)

        confirm_button = QPushButton(
            "Вернуть заводской HLS"
        )
        confirm_button.setObjectName("dangerButton")
        confirm_button.clicked.connect(dialog.accept)

        buttons_layout.addWidget(cancel_button)
        buttons_layout.addWidget(confirm_button)

        dialog_layout.addWidget(title)
        dialog_layout.addWidget(description)
        dialog_layout.addWidget(warning)
        dialog_layout.addLayout(buttons_layout)

        if dialog.exec() != QDialog.DialogCode.Accepted:
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
        self.export_button.setEnabled(
            self._work_mode == WorkMode.PERSONALIZATION
        )


def apply_application_style(app: QApplication) -> None:
    """Задаёт единый спокойный стиль приложения."""

    app.setStyleSheet(
        """
        QWidget {
            color: #363630;
            font-family: "Segoe UI";
            font-size: 14px;
        }

        QWidget#rootContainer {
            background-color: #F2F3F5;
        }

        QDialog#confirmationDialog {
            background-color: #F2F3F5;
        }

        QLabel#confirmationTitle {
            font-size: 18px;
            font-weight: 700;
        }

        QLabel#confirmationWarning {
            color: #9F1C14;
            background-color: #FDECEA;
            border-left: 4px solid #C63228;
            border-radius: 6px;
            padding: 8px 10px;
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
            color: #51595C;
        }

        QLabel#connectionStatus {
            color: #007A5E;
            background-color: #E0F7ED;
            border-radius: 8px;
            padding: 8px 12px;
        }

        QFrame#modeSwitch {
            background-color: white;
            border: 1px solid #9DE4C0;
            border-radius: 10px;
        }

        QFrame#modeSelector {
            background-color: #E0F7ED;
            border: 1px solid #9DE4C0;
            border-radius: 8px;
        }

        QPushButton#modeButton {
            min-height: 30px;
            padding: 0 11px;
            color: #53616A;
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
        }

        QPushButton#modeButton:hover {
            color: #007A5E;
        }

        QPushButton#modeButton:checked {
            color: white;
            background-color: #009975;
            border: 1px solid #009975;
        }

        QLabel#modeIndicator {
            color: #007A5E;
            font-size: 12px;
            font-weight: 600;
        }

        QFrame#operationPanel {
            background-color: #E0F7ED;
            border: 1px solid #9DE4C0;
            border-radius: 8px;
        }

        QLabel#operationTitle {
            color: #007A5E;
            font-weight: 700;
        }

        QLabel#operationStage {
            color: #51595C;
        }

        QLabel#operationElapsed {
            color: #007A5E;
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
            color: #007A5E;
            background-color: #E0F7ED;
            border: 1px solid #9DE4C0;
        }

        QPushButton#readButton:hover,
        QPushButton#secondaryButton:hover {
            color: #007A5E;
            background-color: #F2FBF6;
            border: 1px solid #00DB78;
        }

        QPushButton#primaryButton {
            color: white;
            background-color: #009975;
            border: 1px solid #009975;
        }

        QPushButton#primaryButton:hover {
            background-color: #007A5E;
            border: 1px solid #007A5E;
        }

        QPushButton#primaryButton:pressed {
            background-color: #007A5E;
            border: 1px solid #007A5E;
        }

        QPushButton#dangerButton {
            color: white;
            background-color: #C63228;
            border: 1px solid #C63228;
        }

        QPushButton#secondaryButton {
            color: #007A5E;
            background-color: white;
            border: 1px solid #9DE4C0;
        }

        QPushButton#readButton:disabled,
        QPushButton#primaryButton:disabled,
        QPushButton#secondaryButton:disabled,
        QPushButton#dangerButton:disabled {
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
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    apply_application_style(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
