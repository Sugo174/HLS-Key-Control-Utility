"""Пути к данным приложения в исходном коде и собранной версии."""

import sys

from pathlib import Path


def get_application_directory() -> Path:
    """Возвращает папку рядом с EXE либо корень исходного проекта."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parents[2]


def get_application_data_directory() -> Path:
    """Возвращает папку для локальных файлов приложения."""
    return get_application_directory()
