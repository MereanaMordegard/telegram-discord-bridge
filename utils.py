"""
Вспомогательные утилиты
"""
from datetime import datetime
from typing import Optional
import re


def sanitize_text(text: str, max_length: int = 4000) -> str:
    """
    Очистка и обрезка текста

    Args:
        text: Исходный текст
        max_length: Максимальная длина

    Returns:
        Очищенный текст
    """
    # Удаляем лишние пробелы
    text = re.sub(r'\s+', ' ', text).strip()

    # Обрезаем если слишком длинный
    if len(text) > max_length:
        text = text[:max_length - 3] + "..."

    return text


def format_timestamp(dt: Optional[datetime] = None) -> str:
    """
    Форматировать timestamp

    Args:
        dt: Datetime объект (None = сейчас)

    Returns:
        Отформатированная строка
    """
    if dt is None:
        dt = datetime.now()

    return dt.strftime("%Y-%m-%d %H:%M:%S")


def escape_markdown(text: str) -> str:
    """
    Экранировать Markdown специальные символы

    Args:
        text: Исходный текст

    Returns:
        Экранированный текст
    """
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)


def truncate_filename(filename: str, max_length: int = 100) -> str:
    """
    Обрезать длинное имя файла

    Args:
        filename: Имя файла
        max_length: Максимальная длина

    Returns:
        Обрезанное имя
    """
    if len(filename) <= max_length:
        return filename

    # Сохраняем расширение
    parts = filename.rsplit('.', 1)
    if len(parts) == 2:
        name, ext = parts
        max_name_length = max_length - len(ext) - 4  # -4 для "..." и точки
        return f"{name[:max_name_length]}...{ext}"
    else:
        return filename[:max_length - 3] + "..."