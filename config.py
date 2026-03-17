"""
Конфигурация бота с валидацией
"""
import os
from typing import Optional, List
from dotenv import load_dotenv
from logger import get_logger

load_dotenv()
logger = get_logger(__name__)


class ConfigError(Exception):
    """Ошибка конфигурации"""
    pass


class Config:
    """Класс конфигурации с валидацией"""

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: int
    TELEGRAM_THREAD_ID: Optional[int]

    # Discord
    DISCORD_BOT_TOKEN: str
    DISCORD_CHANNEL_ID: int
    DISCORD_WEBHOOK_URL: Optional[str]

    # VK
    VK_ACCESS_TOKEN: str
    VK_GROUP_ID: int
    VK_ENABLED: bool

    # Security
    MAX_MESSAGE_LENGTH: int
    MAX_FILE_SIZE_MB: int
    ALLOWED_FILE_TYPES: List[str]

    # Rate Limiting
    RATE_LIMIT_MESSAGES: int
    RATE_LIMIT_PERIOD: int

    # Database
    DATABASE_PATH: str

    def __init__(self):
        """Инициализация и валидация конфигурации"""
        self._load_config()
        self._validate_config()

    def _load_config(self):
        """Загрузка конфигурации из .env"""
        # Telegram
        self.TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.TELEGRAM_CHAT_ID = int(os.getenv('TELEGRAM_CHAT_ID', '0'))
        thread_id = os.getenv('TELEGRAM_THREAD_ID', '')
        self.TELEGRAM_THREAD_ID = int(thread_id) if thread_id else None

        # Discord
        self.DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN', '')
        self.DISCORD_CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', '0'))
        self.DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', None)

        # VK
        self.VK_ACCESS_TOKEN = os.getenv('VK_ACCESS_TOKEN', '')
        vk_group_id = os.getenv('VK_GROUP_ID', '0')
        self.VK_GROUP_ID = int(vk_group_id) if vk_group_id else 0
        self.VK_ENABLED = os.getenv('VK_ENABLED', 'false').lower() == 'true'

        # Security
        self.MAX_MESSAGE_LENGTH = int(os.getenv('MAX_MESSAGE_LENGTH', '4000'))
        self.MAX_FILE_SIZE_MB = int(os.getenv('MAX_FILE_SIZE_MB', '10'))
        allowed_types = os.getenv('ALLOWED_FILE_TYPES', 'image,video,audio,document')
        self.ALLOWED_FILE_TYPES = [t.strip() for t in allowed_types.split(',')]

        # Rate Limiting
        self.RATE_LIMIT_MESSAGES = int(os.getenv('RATE_LIMIT_MESSAGES', '30'))
        self.RATE_LIMIT_PERIOD = int(os.getenv('RATE_LIMIT_PERIOD', '60'))

        # Database
        self.DATABASE_PATH = os.getenv('DATABASE_PATH', './bridge_history.db')

    def _validate_config(self):
        """Валидация критических параметров"""
        errors = []

        if not self.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN не установлен")

        if not self.TELEGRAM_CHAT_ID:
            errors.append("TELEGRAM_CHAT_ID не установлен")

        if not self.DISCORD_BOT_TOKEN:
            errors.append("DISCORD_BOT_TOKEN не установлен")

        if not self.DISCORD_CHANNEL_ID:
            errors.append("DISCORD_CHANNEL_ID не установлен")

        # VK опциональный (ДОБАВЬТЕ)
        if self.VK_ENABLED:
            if not self.VK_ACCESS_TOKEN:
                errors.append("VK_ENABLED=true, но VK_ACCESS_TOKEN не установлен")
            if not self.VK_GROUP_ID:
                errors.append("VK_ENABLED=true, но VK_GROUP_ID не установлен")

        if errors:
            error_msg = "Ошибки конфигурации:\n" + "\n".join(f"- {e}" for e in errors)
            logger.error(error_msg)
            raise ConfigError(error_msg)

        logger.info("✅ Конфигурация успешно загружена и валидирована")

    def display_config(self):
        """Отобразить конфигурацию (без токенов)"""
        logger.info("=" * 50)
        logger.info("КОНФИГУРАЦИЯ БОТА")
        logger.info("=" * 50)
        logger.info(f"Telegram Chat ID: {self.TELEGRAM_CHAT_ID}")
        logger.info(f"Telegram Thread ID: {self.TELEGRAM_THREAD_ID or 'Основной чат'}")
        logger.info(f"Discord Channel ID: {self.DISCORD_CHANNEL_ID}")
        logger.info(f"Max Message Length: {self.MAX_MESSAGE_LENGTH}")
        logger.info(f"Max File Size: {self.MAX_FILE_SIZE_MB} MB")
        logger.info(f"Allowed File Types: {', '.join(self.ALLOWED_FILE_TYPES)}")
        logger.info(f"Rate Limit: {self.RATE_LIMIT_MESSAGES} msg/{self.RATE_LIMIT_PERIOD}s")
        logger.info("=" * 50)

        if self.VK_ENABLED:
            logger.info(f"VK Group ID: {self.VK_GROUP_ID}")
            logger.info(f"VK Status: ✅ Включен")
        else:
            logger.info(f"VK Status: ⏸️ Отключен")

        logger.info(f"Max Message Length: {self.MAX_MESSAGE_LENGTH}")


# Глобальный экземпляр конфигурации
config = Config()