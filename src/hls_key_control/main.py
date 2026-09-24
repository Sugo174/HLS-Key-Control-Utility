"""Главное окно HLS Key Control Utility."""

import sys
import time

from datetime import UTC

from PySide6.QtCore import (
    QObject,
    QSettings,
    QThread,
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
        """Считывает серийный номер и передаёт результат окну."""
        started_at = time.monotonic()

        try:
            serial_number = read_meter_serial_number(
                self._port,
                self.progress.emit,
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            elapsed_seconds = time.monotonic() - started_at
            self.succeeded.emit(serial_number, elapsed_seconds)
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

        connection_status = QLabel("● Тестовый режим")
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

        card_title = QLabel("Подключённый прибор")
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

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)

        self.read_button = QPushButton("Считать номер")
        self.read_button.setObjectName("readButton")
        self.read_button.clicked.connect(self._read_meter_number)

        self.write_button = QPushButton("Записать новый HLS")
        self.write_button.setObjectName("primaryButton")
        self.write_button.setEnabled(False)
        self.write_button.clicked.connect(self._reserve_new_hls)

        self.reset_button = QPushButton("Сбросить HLS")
        self.reset_button.setObjectName("secondaryButton")
        self.reset_button.setEnabled(False)
        self.reset_button.clicked.connect(self._reset_hls_for_test)

        buttons_layout.addWidget(self.read_button)
        buttons_layout.addWidget(self.write_button)
        buttons_layout.addWidget(self.reset_button)
        buttons_layout.addStretch()

        self.system_status_label = QLabel(
            "Реестр: SQL Server · Режим: тестовый"
        )
        self.system_status_label.setObjectName("systemStatus")

        root_layout.addLayout(header_layout)
        root_layout.addWidget(meter_card)
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

        if self._read_thread is not None:
            return

        self.read_button.setEnabled(False)
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.meter_status_label.setText(
            f"Считывание номера ПУ через {selected_port}..."
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
        """Показывает текущий безопасный этап чтения."""
        self.meter_status_label.setText(message)

    def _apply_read_serial_number(
        self,
        serial_number: str,
        elapsed_seconds: float,
    ) -> None:
        """Регистрирует успешно считанный ПУ и обновляет окно."""
        meter, was_created = self.registry.get_or_create(serial_number)

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
                self.meter_status_label.setText(
                    f"ПУ считан за {elapsed_seconds:.1f} с и внесён "
                    "в реестр. Доступна запись нового HLS."
                )
            else:
                self.meter_status_label.setText(
                    f"ПУ считан за {elapsed_seconds:.1f} с и уже есть "
                    "в реестре. Доступна запись нового HLS."
                )

            self.system_status_label.setText(
                "Реестр: SQL Server · ПУ считан"
            )
            self.write_button.setEnabled(True)
            self.reset_button.setEnabled(False)
            return

        if meter.status == MeterStatus.OPERATION_UNCONFIRMED:
            self.meter_status_label.setText(
                "Новый HLS сформирован, но запись в ПУ не подтверждена. "
                "Повторная запись заблокирована."
            )
            self.write_button.setEnabled(False)
            self.reset_button.setEnabled(True)
            return

        self.meter_status_label.setText(
            "Текущий статус ПУ не позволяет записать новый HLS."
        )
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(False)

    def _handle_read_failure(self, error_text: str) -> None:
        """Показывает безопасное сообщение об ошибке чтения."""
        self.current_meter = None
        self.serial_label.setText("Серийный номер: —")
        self.meter_status_label.setText(
            "Не удалось считать номер подключённого ПУ."
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

    def _reserve_new_hls(self) -> None:
        """Создаёт HLS и переводит ПУ в состояние ожидания подтверждения."""

        if self.current_meter is None:
            return

        try:
            self.hls_registry.reserve_new_key(self.current_meter)
        except ValueError as error:
            self.meter_status_label.setText(str(error))
            self.write_button.setEnabled(False)
            return

        self._refresh_operations()

        self.meter_status_label.setText(
            "Новый HLS сформирован и зарезервирован. "
            "Запись в ПУ пока не подтверждена. "
            "Повторная запись заблокирована."
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · HLS: зарезервирован"
        )
        self.write_button.setEnabled(False)
        self.reset_button.setEnabled(True)

    def _reset_hls_for_test(self) -> None:
        """Имитирует подтверждённый сервисный сброс в тестовом режиме."""

        if self.current_meter is None:
            return

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Подтверждение сброса HLS")
        dialog.setText(
            "Тестовый сброс отзовёт текущий HLS и разрешит "
            "создание нового пароля для этого ПУ. Продолжить?"
        )

        dialog.addButton(
            "Отмена",
            QMessageBox.ButtonRole.RejectRole,
        )
        confirm_button = dialog.addButton(
            "Сбросить HLS",
            QMessageBox.ButtonRole.DestructiveRole,
        )

        dialog.exec()

        if dialog.clickedButton() is not confirm_button:
            return

        try:
            self.hls_registry.confirm_reset_for_test(self.current_meter)
        except ValueError as error:
            self.meter_status_label.setText(str(error))
            return

        self._refresh_operations()

        self.meter_status_label.setText(
            "HLS сброшен в тестовом режиме. Предыдущий ключ отозван. "
            "Доступна запись нового HLS."
        )
        self.system_status_label.setText(
            "Реестр: SQL Server · HLS: предыдущий ключ отозван"
        )
        self.write_button.setEnabled(True)
        self.reset_button.setEnabled(False)


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