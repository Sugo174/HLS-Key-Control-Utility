"""Поиск последовательных портов, доступных в Windows."""

from dataclasses import dataclass

from serial.tools import list_ports


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    """Краткая информация об одном COM-порте."""

    device: str
    description: str


def list_available_serial_ports() -> list[SerialPortInfo]:
    """Возвращает все доступные COM-порты."""
    ports = [
        SerialPortInfo(
            device=port.device,
            description=port.description or "Неизвестное устройство",
        )
        for port in list_ports.comports()
    ]

    return sorted(ports, key=lambda port: port.device)