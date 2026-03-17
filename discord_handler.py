"""
Обработчик Discord сообщений
"""
import discord
from discord.ext import commands
from discord import Webhook
import aiohttp
import io
from typing import Optional
from logger import get_logger
from config import config
from database import db
from media_handler import media_handler
from message_queue import telegram_queue

logger = get_logger(__name__)


class DiscordHandler:
    """Класс для обработки Discord событий"""

    def __init__(self, telegram_sender):
        self.bot: Optional[commands.Bot] = None
        self.telegram_sender = telegram_sender
        self.webhook: Optional[Webhook] = None
        self.is_running = False

        # Intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True

        self.bot = commands.Bot(command_prefix='!', intents=intents)

        # Регистрируем обработчики событий
        self.bot.event(self.on_ready)
        self.bot.event(self.on_message)
        self.bot.event(self.on_error)

    async def initialize(self):
        """Инициализация Discord бота"""
        try:
            # Инициализация webhook если указан
            if config.DISCORD_WEBHOOK_URL:
                await self._init_webhook()

            logger.info("✅ Discord handler инициализирован")

        except Exception as e:
            logger.error(f"❌ Ошибка инициализации Discord: {e}")
            raise

    async def _init_webhook(self):
        """Инициализация webhook для улучшенного отображения"""
        try:
            async with aiohttp.ClientSession() as session:
                self.webhook = Webhook.from_url(
                    config.DISCORD_WEBHOOK_URL,
                    session=session
                )
                # Проверяем webhook
                await self.webhook.fetch()
                logger.info("✅ Discord webhook инициализирован")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось инициализировать webhook: {e}")
            self.webhook = None

    async def on_ready(self):
        """Событие готовности бота"""
        logger.info(f"✅ Discord бот запущен: {self.bot.user.name}")
        logger.info(f"📊 Подключен к {len(self.bot.guilds)} серверам")

        # Проверяем доступность канала
        channel = self.bot.get_channel(config.DISCORD_CHANNEL_ID)
        if channel:
            logger.info(f"✅ Целевой канал найден: #{channel.name}")
        else:
            logger.error(f"❌ Канал {config.DISCORD_CHANNEL_ID} не найден!")

    async def on_message(self, message: discord.Message):
        """Обработка входящего сообщения"""
        try:
            # Игнорируем сообщения от ботов
            if message.author.bot:
                return

            # ЗАМЕНИТЕ ЭТИ СТРОКИ:
            # Проверяем что сообщение из текстового канала (игнорируем треды, форумы)
            if not isinstance(message.channel, discord.TextChannel):
                return

            # Игнорируем определённые каналы (опционально)
            ignored_channels = ['📜-правила', '📢-объявления']  # Список каналов для игнора
            if message.channel.name in ignored_channels:
                return

            # Получаем имя пользователя
            username = self._get_username(message.author)

            # Получаем название канала
            channel_name = message.channel.name

            # Получаем текст
            text = message.content

            # Обрабатываем вложения
            media_info = await self._process_attachments(message)

            # Отправляем в Telegram через очередь
            await telegram_queue.add(
                self.telegram_sender.send_message,
                text=text,
                username=username,
                channel_name=channel_name,
                media_info=media_info,
                discord_message_id=str(message.id)
            )

            logger.info(
                f"📨 Discord → TG: {username}: "
                f"{text[:50]}{'...' if len(text) > 50 else ''}"
            )

        except Exception as e:
            logger.error(f"❌ Ошибка обработки Discord сообщения: {e}", exc_info=True)
            await db.log_error("discord_message_handler", str(e))

    async def on_error(self, event: str, *args, **kwargs):
        """Обработка ошибок Discord"""
        logger.error(f"❌ Discord error in {event}: {args} {kwargs}")

    def _get_username(self, author: discord.User) -> str:
        """Получить имя пользователя"""
        if hasattr(author, 'nick') and author.nick:
            return author.nick
        elif author.global_name:
            return author.global_name
        else:
            return author.name

    async def _process_attachments(self, message: discord.Message) -> Optional[dict]:
        """Обработка вложений из сообщения"""
        try:
            if not message.attachments:
                return None

            # Берём первое вложение
            attachment = message.attachments[0]

            # Проверяем размер
            size_mb = attachment.size / 1024 / 1024
            if size_mb > config.MAX_FILE_SIZE_MB:
                logger.warning(f"⚠️ Файл слишком большой: {size_mb:.2f}MB")
                return None

            # Определяем тип
            file_type = media_handler.get_file_type(attachment.filename)

            # Проверяем разрешён ли тип
            if not media_handler.is_allowed_file_type(file_type):
                logger.warning(f"⚠️ Тип файла не разрешён: {file_type}")
                return None

            # Скачиваем файл
            file_data = await media_handler.download_file(attachment.url)

            if not file_data:
                return None

            # Сжимаем изображения
            if file_type == 'image':
                file_data = media_handler.compress_image(file_data)

            return {
                'type': file_type,
                'data': file_data,
                'filename': attachment.filename
            }

        except Exception as e:
            logger.error(f"❌ Ошибка обработки вложений: {e}")
            return None

    async def send_message(
            self,
            username: str,
            text: str = "",
            media_info: Optional[dict] = None,
            telegram_message_id: Optional[str] = None
    ):
        """
        Отправка сообщения в Discord

        Args:
            username: Имя отправителя из Telegram
            text: Текст сообщения
            media_info: Информация о медиафайле
            telegram_message_id: ID сообщения в Telegram
        """
        try:
            channel = self.bot.get_channel(config.DISCORD_CHANNEL_ID)

            if not channel:
                logger.error(f"❌ Канал {config.DISCORD_CHANNEL_ID} не найден")
                return

            # Обрезаем текст если слишком длинный
            if len(text) > config.MAX_MESSAGE_LENGTH:
                text = text[:config.MAX_MESSAGE_LENGTH - 3] + "..."

            sent_message = None

            # Используем webhook если доступен
            if self.webhook and config.DISCORD_WEBHOOK_URL:
                sent_message = await self._send_via_webhook(
                    username, text, media_info
                )
            else:
                sent_message = await self._send_via_bot(
                    channel, username, text, media_info
                )

            # Логируем в БД
            if sent_message:
                await db.log_message(
                    source='telegram',
                    destination='discord',
                    source_id=telegram_message_id or 'unknown',
                    destination_id=str(sent_message.id),
                    username=username,
                    content=text,
                    media_type=media_info['type'] if media_info else None
                )

                logger.info(f"✅ TG → Discord: {username}")

        except discord.HTTPException as e:
            logger.error(f"❌ Discord HTTP ошибка: {e}")
            await db.log_error("discord_http", str(e))

        except discord.Forbidden:
            logger.error("❌ Discord: нет прав для отправки сообщений")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки в Discord: {e}", exc_info=True)
            await db.log_error("discord_send_general", str(e))

    async def _send_via_webhook(
            self,
            username: str,
            text: str,
            media_info: Optional[dict]
    ) -> Optional[discord.WebhookMessage]:
        """Отправка через webhook (лучшее отображение)"""
        try:
            async with aiohttp.ClientSession() as session:
                webhook = Webhook.from_url(
                    config.DISCORD_WEBHOOK_URL,
                    session=session
                )

                # Формируем сообщение
                content = f"**[Telegram]** {text}" if text else None

                # Отправляем с файлом или без
                if media_info:
                    file = discord.File(
                        io.BytesIO(media_info['data']),
                        filename=media_info['filename']
                    )
                    return await webhook.send(
                        content=content,
                        username=username,
                        file=file,
                        wait=True
                    )
                elif content:
                    return await webhook.send(
                        content=content,
                        username=username,
                        wait=True
                    )

        except Exception as e:
            logger.error(f"❌ Ошибка отправки через webhook: {e}")
            return None

    async def _send_via_bot(
            self,
            channel: discord.TextChannel,
            username: str,
            text: str,
            media_info: Optional[dict]
    ) -> Optional[discord.Message]:
        """Отправка через обычного бота"""
        try:
            # Формируем текст
            formatted_text = f"**[Telegram] {username}:** {text}" if text else f"**[Telegram] {username}:**"

            # Отправляем с файлом или без
            if media_info:
                file = discord.File(
                    io.BytesIO(media_info['data']),
                    filename=media_info['filename']
                )
                return await channel.send(content=formatted_text, file=file)
            elif text:
                return await channel.send(content=formatted_text)

        except Exception as e:
            logger.error(f"❌ Ошибка отправки через бота: {e}")
            return None

    async def start(self):
        """Запуск бота"""
        self.is_running = True
        await self.bot.start(config.DISCORD_BOT_TOKEN)

    async def stop(self):
        """Остановка бота"""
        logger.info("🛑 Останавливаю Discord бота...")
        self.is_running = False

        if self.bot and not self.bot.is_closed():
            try:
                await self.bot.close()
                logger.info("✅ Discord бот остановлен")
            except Exception as e:
                logger.error(f"❌ Ошибка остановки Discord: {e}")