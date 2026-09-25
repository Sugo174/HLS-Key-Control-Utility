"""Простой локальный журнал итоговых действий приложения."""

from datetime import datetime
from pathlib import Path


def write_application_log(message: str) -> None:
    """Добавляет строку в локальный журнал текущего дня."""
    try:
        current_time = datetime.now().astimezone()
        log_directory = Path.cwd() / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)

        log_path = log_directory / (
            f"{current_time:%Y-%m-%d}.txt"
        )

        normalized_message = " ".join(message.split())

        with log_path.open(
            "a",
            encoding="utf-8",
        ) as log_file:
            log_file.write(
                f"{current_time:%H:%M:%S} · "
                f"{normalized_message}\n"
            )
    except OSError:
        # Локальный журнал не должен мешать работе с ПУ.
        pass