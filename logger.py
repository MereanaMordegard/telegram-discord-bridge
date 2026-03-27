"""
Централизованное логирование для бота
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()


class BridgeLogger:
    """Класс для настройки логирования"""

    def __init__(self):
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')
        self.log_file = os.getenv('LOG_FILE', './bridge.log')
        self._setup_logger()

    def _setup_logger(self):
        """Настройка логгера с ротацией файлов"""
        # Создаём директорию для логов
        log_path = Path(self.log_file).parent
        log_path.mkdir(parents=True, exist_ok=True)

        # Формат логов
        log_format = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Корневой логгер
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.log_level))

        # Удаляем существующие handlers
        root_logger.handlers.clear()

        # Handler для файла (с ротацией)
        file_handler = RotatingFileHandler(
            self.log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=2,
            encoding='utf-8'
        )
        file_handler.setFormatter(log_format)
        root_logger.addHandler(file_handler)

        # Handler для консоли
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(log_format)
        root_logger.addHandler(console_handler)

        # Снижаем уровень логирования библиотек
        logging.getLogger('discord').setLevel(logging.WARNING)
        logging.getLogger('telegram').setLevel(logging.WARNING)
        logging.getLogger('aiohttp').setLevel(logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Получить логгер для модуля"""
    return logging.getLogger(name)