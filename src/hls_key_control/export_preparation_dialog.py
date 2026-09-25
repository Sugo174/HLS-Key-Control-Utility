"""Окно просмотра и подготовки экспортной партии HLS."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from hls_key_control.services.export_preparation import (
    ExportPreviewItem,
)


class ExportDetailsDialog(QDialog):
    """Запрашивает реквизиты создаваемого Excel-файла."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setWindowTitle("Параметры экспортного файла")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        description = QLabel(
            "Укажите реквизиты партии. Они будут сохранены "
            "в реестре и добавлены в имя Excel-файла."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        form_layout = QFormLayout()
        form_layout.setSpacing(10)

        self.customer_name_edit = QLineEdit()
        self.customer_name_edit.setPlaceholderText(
            "Например: ООО «Энергосеть»"
        )

        self.order_number_edit = QLineEdit()
        self.order_number_edit.setPlaceholderText(
            "Например: ЗК-2026-014"
        )

        form_layout.addRow("Заказчик:", self.customer_name_edit)
        form_layout.addRow("Номер заказа:", self.order_number_edit)
        layout.addLayout(form_layout)

        self.validation_label = QLabel()
        self.validation_label.setStyleSheet("color: #9F1C14;")
        self.validation_label.setWordWrap(True)
        self.validation_label.hide()
        layout.addWidget(self.validation_label)

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        cancel_button = QPushButton("Отмена")
        cancel_button.setObjectName("secondaryButton")
        cancel_button.clicked.connect(self.reject)

        self.confirm_button = QPushButton(
            "Экспортировать в Excel"
        )
        self.confirm_button.setObjectName("primaryButton")
        self.confirm_button.clicked.connect(self._confirm)

        buttons_layout.addWidget(cancel_button)
        buttons_layout.addWidget(self.confirm_button)
        layout.addLayout(buttons_layout)

    @property
    def customer_name(self) -> str:
        return self.customer_name_edit.text().strip()

    @property
    def order_number(self) -> str:
        return self.order_number_edit.text().strip()

    def _confirm(self) -> None:
        if not self.customer_name:
            self._show_validation("Укажите заказчика.")
            self.customer_name_edit.setFocus()
            return

        if not self.order_number:
            self._show_validation("Укажите номер заказа.")
            self.order_number_edit.setFocus()
            return

        self.accept()

    def _show_validation(self, text: str) -> None:
        self.validation_label.setText(text)
        self.validation_label.show()


class ExportPreparationDialog(QDialog):
    """Показывает подтверждённые HLS, доступные для выгрузки."""

    def __init__(
        self,
        preview_items: list[ExportPreviewItem],
        parent=None,
    ) -> None:
        super().__init__(parent)

        self._preview_items = preview_items
        self.customer_name = ""
        self.order_number = ""

        self.setWindowTitle("Подготовка выгрузки HLS")
        self.setMinimumSize(720, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        title = QLabel("Подтверждённые HLS для выгрузки")
        title.setStyleSheet(
            "font-size: 18px; font-weight: 700;"
        )
        layout.addWidget(title)

        self.summary_label = QLabel(
            f"К выгрузке: {len(preview_items)} счётч."
        )
        self.summary_label.setStyleSheet("color: #61708A;")
        layout.addWidget(self.summary_label)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "Поиск по серийному номеру счётчика..."
        )
        self.search_edit.textChanged.connect(self._filter_rows)
        layout.addWidget(self.search_edit)

        self.table = QTableWidget(len(preview_items), 2)
        self.table.setHorizontalHeaderLabels(
            [
                "Серийный номер счётчика",
                "Новый HLS",
            ]
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        header.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Stretch,
        )

        for row_number, item in enumerate(preview_items):
            serial_item = QTableWidgetItem(item.serial_number)
            password_item = QTableWidgetItem(item.hls_password)

            serial_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter
                | Qt.AlignmentFlag.AlignLeft
            )
            password_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter
                | Qt.AlignmentFlag.AlignLeft
            )

            self.table.setItem(row_number, 0, serial_item)
            self.table.setItem(row_number, 1, password_item)

        layout.addWidget(self.table)

        self.copy_status_label = QLabel(
            "Выберите строку и скопируйте HLS кнопкой "
            "или сочетанием Ctrl+C."
        )
        self.copy_status_label.setStyleSheet("color: #61708A;")
        layout.addWidget(self.copy_status_label)

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)

        copy_button = QPushButton("Скопировать HLS")
        copy_button.setObjectName("secondaryButton")
        copy_button.clicked.connect(self._copy_selected_hls)

        buttons_layout.addWidget(copy_button)
        buttons_layout.addStretch()

        close_button = QPushButton("Закрыть")
        close_button.setObjectName("secondaryButton")
        close_button.clicked.connect(self.reject)

        export_button = QPushButton("Экспортировать в Excel")
        export_button.setObjectName("primaryButton")
        export_button.clicked.connect(self._request_export)

        buttons_layout.addWidget(close_button)
        buttons_layout.addWidget(export_button)
        layout.addLayout(buttons_layout)

        copy_shortcut = QShortcut(
            QKeySequence.StandardKey.Copy,
            self,
        )
        copy_shortcut.activated.connect(self._copy_selected_hls)

    def _filter_rows(self, search_text: str) -> None:
        needle = search_text.strip()

        for row_number in range(self.table.rowCount()):
            serial_number = self.table.item(
                row_number,
                0,
            ).text()

            self.table.setRowHidden(
                row_number,
                bool(needle and needle not in serial_number),
            )

    def _copy_selected_hls(self) -> None:
        row_number = self.table.currentRow()

        if row_number < 0:
            QMessageBox.information(
                self,
                "Не выбрана строка",
                "Сначала выберите счётчик в таблице.",
            )
            return

        hls_password = self.table.item(
            row_number,
            1,
        ).text()

        QApplication.clipboard().setText(hls_password)
        self.copy_status_label.setText(
            "HLS выбранного счётчика скопирован в буфер обмена."
        )
        self.copy_status_label.setStyleSheet(
            "color: #075B32;"
        )

    def _request_export(self) -> None:
        details_dialog = ExportDetailsDialog(self)

        if details_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.customer_name = details_dialog.customer_name
        self.order_number = details_dialog.order_number
        self.accept()