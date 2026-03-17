"""
Обработчик Telegram сообщений
"""
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import TelegramError, RetryAfter, TimedOut
import asyncio
import io
from typing import Optional
from logger import get_logger
from config import config
from database import db
from media_handler import media_handler
from message_queue import discord_queue

logger = get_logger(__name__)


class TelegramHandler:
    """Класс для обработки Telegram событий"""

    def __init__(self, discord_sender, vk_sender=None):
        self.bot: Optional[Bot] = None
        self.application = None
        self.discord_sender = discord_sender
        self.vk_sender = vk_sender
        self.is_running = False

    async def initialize(self):
        """Инициализация Telegram бота"""
        try:
            # Создаём с увеличенными таймаутами
            from telegram.request import HTTPXRequest

            request = HTTPXRequest(
                connection_pool_size=8,
                connect_timeout=30.0,
                read_timeout=30.0,
                write_timeout=30.0,
                pool_timeout=30.0
            )

            self.application = (
                ApplicationBuilder()
                .token(config.TELEGRAM_BOT_TOKEN)
                .request(request)
                .build()
            )

            self.bot = self.application.bot

            # Добавляем обработчики (только текст и медиа, игнорируем остальное)
            # Добавляем обработчики
            self.application.add_handler(
                MessageHandler(
                    filters.ALL & ~filters.COMMAND,
                    self._handle_message
                )
            )

            # Проверяем подключение
            me = await self.bot.get_me()
            logger.info(f"✅ Telegram бот запущен: @{me.username}")

        except Exception as e:
            logger.error(f"❌ Ошибка инициализации Telegram: {e}")
            raise

    def _is_topic_auto_reply(self, message) -> bool:
        """Проверить является ли reply автоматической привязкой к теме форума"""
        reply = message.reply_to_message
        if not reply:
            return False
        if message.message_thread_id and reply.message_id == message.message_thread_id:
            return True
        if reply.forum_topic_created or reply.forum_topic_edited:
            return True
        return False

    async def _handle_message(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE
    ):
        """Обработка входящего сообщения"""
        try:
            # Получаем сообщение (может быть обычное или редактированное)
            is_edit = False
            message = update.message
            if not message:
                message = update.edited_message
                is_edit = True

            if not message:
                logger.debug("⏭️ Пропуск: нет сообщения в update")
                return

            if is_edit:
                from datetime import datetime, timezone, timedelta
                message_age = datetime.now(timezone.utc) - message.date
                if message_age > timedelta(minutes=10):
                    logger.debug(f"⏭️ Пропуск редактирования старого сообщения (возраст {message_age})")
                    return

            # Проверяем что есть chat
            if not message.chat:
                logger.debug("⏭️ Пропуск: нет chat в сообщении")
                return

            # Проверяем что сообщение из нужного чата
            if message.chat.id != config.TELEGRAM_CHAT_ID:
                logger.debug(f"⏭️ Пропуск: сообщение из другого чата {message.chat.id}")
                return

            # Игнорируем сообщения от самого бота
            if message.from_user and message.from_user.is_bot:
                if message.from_user.id == self.bot.id:
                    logger.debug("⏭️ Пропуск: сообщение от самого себя")
                    return

            # Игнорируем если нет автора
            if not message.from_user:
                logger.debug("⏭️ Пропуск: нет автора")
                return

            # Получаем информацию об отправителе
            username = self._get_username(message)

            # Обрабатываем текст
            text = message.text or message.caption or ""
            if is_edit and text:
                text = f"✏️ (ред.) {text}"

            # Эмодзи для стикеров
            if not text and message.sticker and message.sticker.emoji:
                text = message.sticker.emoji

            # Геопозиция
            if message.location:
                lat = message.location.latitude
                lon = message.location.longitude
                maps_link = f"https://www.google.com/maps?q={lat},{lon}"
                if message.venue:
                    venue_title = message.venue.title
                    venue_address = message.venue.address or ""
                    text = f"📍 {venue_title}\n{venue_address}\n{maps_link}" if not text else f"{text}\n\n📍 {venue_title}\n{venue_address}\n{maps_link}"
                else:
                    text = f"📍 Геопозиция: {maps_link}" if not text else f"{text}\n\n📍 Геопозиция: {maps_link}"

            # Контакт
            if message.contact:
                contact = message.contact
                contact_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
                contact_phone = contact.phone_number or ""
                text = f"👤 Контакт: {contact_name}\n📞 {contact_phone}" if not text else f"{text}\n\n👤 Контакт: {contact_name}\n📞 {contact_phone}"

            # Опрос
            if message.poll:
                poll = message.poll
                poll_text = f"📊 Опрос: {poll.question}\n"
                for i, option in enumerate(poll.options, 1):
                    poll_text += f"  {i}. {option.text}\n"
                if poll.is_anonymous:
                    poll_text += "(анонимный)"
                text = poll_text if not text else f"{text}\n\n{poll_text}"

            # Ответ на сообщение (цитата)
            if message.reply_to_message and not self._is_topic_auto_reply(message):
                reply = message.reply_to_message
                reply_user = self._get_username(reply) if reply.from_user else "Неизвестный"
                reply_text = reply.text or reply.caption or ""

                # Определяем тип контента если нет текста
                if not reply_text:
                    if reply.photo:
                        reply_text = "🖼 Фото"
                    elif reply.video:
                        reply_text = "🎬 Видео"
                    elif reply.animation:
                        reply_text = "🎞 GIF"
                    elif reply.sticker:
                        emoji = reply.sticker.emoji or ""
                        reply_text = f"🏷 Стикер {emoji}"
                    elif reply.voice:
                        reply_text = "🎤 Голосовое сообщение"
                    elif reply.video_note:
                        reply_text = "📹 Видеосообщение"
                    elif reply.audio:
                        reply_text = f"🎵 Аудио: {reply.audio.title or 'без названия'}"
                    elif reply.document:
                        reply_text = f"📎 Файл: {reply.document.file_name or 'без имени'}"
                    elif reply.poll:
                        reply_text = f"📊 Опрос: {reply.poll.question}"
                    elif reply.location:
                        reply_text = "📍 Геопозиция"
                    elif reply.contact:
                        name = f"{reply.contact.first_name or ''} {reply.contact.last_name or ''}".strip()
                        reply_text = f"👤 Контакт: {name}"
                    else:
                        reply_text = "сообщение"
                else:
                    if len(reply_text) > 200:
                        reply_text = reply_text[:200] + "..."
                    # Если есть и текст и медиа — добавляем пометку
                    if reply.photo:
                        reply_text = f"🖼 {reply_text}"
                    elif reply.video:
                        reply_text = f"🎬 {reply_text}"
                    elif reply.animation:
                        reply_text = f"🎞 {reply_text}"
                    elif reply.document:
                        reply_text = f"📎 {reply_text}"

                quote = f"\n💬 {reply_user}: «{reply_text}»\n\n"
                text = quote + text

            # Обрабатываем медиа
            media_info = await self._process_media(message)

            # Получаем thread_id текущего сообщения
            current_thread_id = message.message_thread_id

            # Фильтрация в треде "Фотографии" — удаляем всё кроме фото/видео
            PHOTO_THREAD_ID = 14101  # тред "Фотографии"

            if current_thread_id == PHOTO_THREAD_ID:
                has_photo_or_video = bool(message.photo or message.video or message.video_note)
                if not has_photo_or_video:
                    # Удаляем сообщение
                    try:
                        await message.delete()
                        logger.info(f"🗑 Удалено не-фото/видео из треда Фотографии: {username}: {text[:50]}")
                    except Exception as e:
                        logger.warning(f"⚠️ Не удалось удалить сообщение из треда Фотографии: {e}")
                    return  # НЕ пересылаем дальше

            # ЛОГИКА ДЛЯ DISCORD:
            # Отправляем в Discord ТОЛЬКО если это тред 38364 (Игромания)
            if config.TELEGRAM_THREAD_ID and current_thread_id == config.TELEGRAM_THREAD_ID:
                await discord_queue.add(
                    self.discord_sender.send_message,
                    username=username,
                    text=text,
                    media_info=media_info,
                    telegram_message_id=str(message.message_id)
                )

                logger.info(
                    f"📨 TG (thread {current_thread_id}) → Discord: {username}: "
                    f"{text[:50]}{'...' if len(text) > 50 else ''}"
                )

            # ЛОГИКА ДЛЯ VK:
            # Отправляем в VK если тред из маппинга (любой синхронизируемый)
            if config.VK_ENABLED and self.vk_sender and current_thread_id:
                from vk_thread_mapping import is_thread_synced

                if is_thread_synced(current_thread_id):
                    await self.vk_sender.queue.add(
                        self.vk_sender.send_message,
                        text=text,
                        username=username,
                        thread_id=current_thread_id,
                        media_info=media_info,
                        telegram_message_id=str(message.message_id)
                    )

                    logger.info(
                        f"📨 TG (thread {current_thread_id}) → VK: {username}: "
                        f"{text[:50]}{'...' if len(text) > 50 else ''}"
                    )
            # ЛОГИКА ДЛЯ СТЕНЫ VK:
            if config.VK_ENABLED and self.vk_sender:
                from vk_thread_mapping import is_wall_thread

                # General topic приходит как thread_id=None, считаем его как 1
                wall_thread_id = current_thread_id or 1

                if is_wall_thread(wall_thread_id):
                    await self.vk_sender.queue.add(
                        self.vk_sender.post_to_wall,
                        text=text,
                        username=username,
                        media_info=media_info,
                        telegram_message_id=str(message.message_id)
                    )

                    logger.info(
                        f"📨 TG (thread {current_thread_id}) → VK стена: {username}: "
                        f"{text[:50]}{'...' if len(text) > 50 else ''}"
                    )

        except Exception as e:
            logger.error(f"❌ Ошибка обработки Telegram сообщения: {e}", exc_info=True)
            await db.log_error("telegram_message_handler", str(e))

    def _get_username(self, message) -> str:
        """Получить имя пользователя"""
        user = message.from_user

        if user.username:
            return f"@{user.username}"
        elif user.first_name and user.last_name:
            return f"{user.first_name} {user.last_name}"
        elif user.first_name:
            return user.first_name
        else:
            return f"User{user.id}"

    async def _process_media(self, message) -> Optional[dict]:
        """Обработка медиафайлов из сообщения"""
        try:
            file_obj = None
            file_type = None
            filename = "file"

            # Определяем тип медиа
            if message.photo:
                file_obj = message.photo[-1]  # Берём самое большое фото
                file_type = 'image'
                filename = f"photo_{message.message_id}.jpg"

            elif message.video:
                file_obj = message.video
                file_type = 'video'
                filename = message.video.file_name or f"video_{message.message_id}.mp4"

            elif message.document:
                file_obj = message.document
                file_type = media_handler.get_file_type(message.document.file_name or "")
                filename = message.document.file_name or f"document_{message.message_id}"

            elif message.audio:
                file_obj = message.audio
                file_type = 'audio'
                filename = message.audio.file_name or f"audio_{message.message_id}.mp3"

            elif message.voice:
                file_obj = message.voice
                file_type = 'audio'
                filename = f"voice_{message.message_id}.ogg"

            elif message.video_note:
                file_obj = message.video_note
                file_type = 'video'
                filename = f"video_note_{message.message_id}.mp4"

            elif message.sticker:
                sticker = message.sticker
                # Анимированные .tgs стикеры пропускаем (нельзя конвертировать просто)
                if sticker.is_animated:
                    return None
                # Видео-стикеры (.webm) тоже пропускаем
                if sticker.is_video:
                    return None
                # Обычный стикер — скачиваем и конвертируем в PNG/JPG
                file_obj = sticker
                file_type = 'image'
                filename = f"sticker_{message.message_id}.webp"

            if not file_obj:
                return None

            # Проверяем разрешён ли тип файла
            if not media_handler.is_allowed_file_type(file_type):
                logger.warning(f"⚠️ Тип файла не разрешён: {file_type}")
                return None

            # Проверяем размер
            file_size_mb = getattr(file_obj, 'file_size', 0) / 1024 / 1024
            if file_size_mb > config.MAX_FILE_SIZE_MB:
                logger.warning(f"⚠️ Файл слишком большой: {file_size_mb:.2f}MB")
                return None

            # Скачиваем файл
            file = await self.bot.get_file(file_obj.file_id)
            file_data = await media_handler.download_file(file.file_path)

            if not file_data:
                return None

            # Сжимаем изображения при необходимости
            if file_type == 'image':
                file_data = media_handler.compress_image(file_data)

            if message.sticker and file_data:
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(file_data))
                    if img.mode in ('RGBA', 'LA', 'P'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                        img = background
                    output = io.BytesIO()
                    img.save(output, format='JPEG', quality=90)
                    file_data = output.getvalue()
                    filename = f"sticker_{message.message_id}.jpg"
                except Exception as e:
                    logger.error(f"❌ Ошибка конвертации стикера: {e}")
                    return None

            return {
                'type': file_type,
                'data': file_data,
                'filename': filename
            }

        except Exception as e:
            logger.error(f"❌ Ошибка обработки медиа: {e}")
            return None

    async def send_message(
            self,
            text: str,
            username: str = "Discord",
            channel_name: str = "discord",
            media_info: Optional[dict] = None,
            discord_message_id: Optional[str] = None,
            thread_id: Optional[int] = None,
            source: str = "Discord",
            **kwargs
    ):
        """
        Отправка сообщения в Telegram

        Args:
            text: Текст сообщения
            username: Имя отправителя
            channel_name: Название канала Discord
            media_info: Информация о медиафайле
            thread_id: ID треда (если нужно отправить в конкретный тред)
            source: Источник (Discord/VK)
            **kwargs: Дополнительные параметры (discord_message_id, vk_message_id и т.д.)
        """
        try:
            # ДОБАВЬТЕ DEBUG
            logger.debug(f"🔍 DEBUG send_message вызван: thread_id={thread_id}, source={source}, username={username}")

            # Если указан thread_id - используем send_message_to_thread
            if thread_id:
                logger.debug(f"🔍 DEBUG: Вызываем send_message_to_thread для треда {thread_id}")

                # Получаем source_message_id из kwargs
                source_message_id = kwargs.get('vk_message_id') or kwargs.get('discord_message_id')

                await self.send_message_to_thread(
                    text=text,
                    username=username,
                    thread_id=thread_id,
                    media_info=media_info,
                    source=source,
                    source_message_id=source_message_id
                )

                logger.debug(f"🔍 DEBUG: send_message_to_thread завершён")
                return

            # Иначе стандартная отправка в Discord-тред (для обратной совместимости)
            # Формируем текст с префиксом (указываем канал)
            if text:
                formatted_text = f"[#{channel_name}] {username}: {text}"
            else:
                formatted_text = f"[#{channel_name}] {username}:"

            # Обрезаем если слишком длинное
            if len(formatted_text) > config.MAX_MESSAGE_LENGTH:
                formatted_text = formatted_text[:config.MAX_MESSAGE_LENGTH - 3] + "..."

            sent_message = None

            # Отправляем медиа или текст
            if media_info:
                sent_message = await self._send_media(formatted_text, media_info)
            elif text:
                sent_message = await self._send_text(formatted_text)

            # Логируем в БД
            if sent_message:
                await db.log_message(
                    source='discord',
                    destination='telegram',
                    source_id=discord_message_id or 'unknown',
                    destination_id=str(sent_message.message_id),
                    username=username,
                    content=text,
                    media_type=media_info['type'] if media_info else None
                )

                logger.info(f"✅ Discord → TG: {username}")


        except RetryAfter as e:
            logger.warning(f"⏳ Rate limit Telegram: ожидание {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            await self.send_message(
                text=text,
                username=username,
                channel_name=channel_name,
                media_info=media_info,
                discord_message_id=discord_message_id,
                thread_id=thread_id,
                source=source,
                **kwargs
            )

        except TimedOut as e:
            logger.warning(f"⏰ Таймаут отправки в Telegram, повторяю попытку...")
            await asyncio.sleep(2)
            # Повторная попытка без медиа (только текст)
            try:
                if text:
                    await self._send_text(f"[Discord] {username}: {text}")
                    logger.info("✅ Сообщение отправлено после повтора (без медиа)")
            except Exception as retry_error:
                logger.error(f"❌ Повторная попытка не удалась: {retry_error}")

        except TelegramError as e:
            logger.error(f"❌ Telegram ошибка: {e}")
            await db.log_error("telegram_send", str(e))

        except Exception as e:
            logger.error(f"❌ Ошибка отправки в Telegram: {e}", exc_info=True)
            await db.log_error("telegram_send_general", str(e))

    async def _send_text(self, text: str):
        """Отправка текстового сообщения"""
        return await self.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text,
            message_thread_id=config.TELEGRAM_THREAD_ID
        )

    async def _send_media(self, caption: str, media_info: dict):
        """Отправка медиафайла"""
        media_type = media_info['type']
        file_data = media_info['data']
        filename = media_info['filename']

        file_size_mb = len(file_data) / 1024 / 1024
        logger.info(f"📤 Отправка {media_type}: {filename} ({file_size_mb:.2f}MB)")

        # Создаём BytesIO объект
        file_obj = io.BytesIO(file_data)
        file_obj.name = filename

        # Отправляем в зависимости от типа
        if media_type == 'image':
            return await self.bot.send_photo(
                chat_id=config.TELEGRAM_CHAT_ID,
                photo=file_obj,
                caption=caption,
                message_thread_id=config.TELEGRAM_THREAD_ID
            )
        elif media_type == 'video':
            return await self.bot.send_video(
                chat_id=config.TELEGRAM_CHAT_ID,
                video=file_obj,
                caption=caption,
                message_thread_id=config.TELEGRAM_THREAD_ID
            )
        elif media_type == 'audio':
            return await self.bot.send_audio(
                chat_id=config.TELEGRAM_CHAT_ID,
                audio=file_obj,
                caption=caption,
                message_thread_id=config.TELEGRAM_THREAD_ID
            )
        else:  # document
            return await self.bot.send_document(
                chat_id=config.TELEGRAM_CHAT_ID,
                document=file_obj,
                caption=caption,
                message_thread_id=config.TELEGRAM_THREAD_ID
            )

    async def send_message_to_thread(
            self,
            text: str,
            username: str,
            thread_id: int,
            media_info: Optional[dict] = None,
            source: str = "VK",
            source_message_id: Optional[str] = None,
            vk_peer_id: Optional[int] = None
    ):
        """Отправка сообщения в конкретный Telegram тред"""
        try:
            logger.debug(f"🔍 send_message_to_thread START")

            # ДОБАВЬТЕ КРИТИЧЕСКУЮ ПРОВЕРКУ В НАЧАЛО
            if not self.bot:
                logger.error("❌ CRITICAL: self.bot is None in send_message_to_thread!")
                logger.error(f"❌ self.application = {self.application}")
                logger.error(f"❌ self.is_running = {self.is_running}")
                return

            logger.debug(f"✅ self.bot OK: {type(self.bot)}")
            logger.debug(f"🔍 text={text[:50] if text else 'None'}")
            logger.debug(f"🔍 DEBUG: text={text[:50] if text else 'None'}")
            logger.debug(f"🔍 DEBUG: username={username}")
            logger.debug(f"🔍 DEBUG: thread_id={thread_id}")
            logger.debug(f"🔍 DEBUG: source={source}")

            # Формируем текст с префиксом
            if text:
                formatted_text = f"[{source}] {username}: {text}"
            else:
                formatted_text = f"[{source}] {username}:"

            logger.debug(f"🔍 DEBUG: formatted_text={formatted_text[:100]}")

            # Обрезаем если слишком длинное
            if len(formatted_text) > config.MAX_MESSAGE_LENGTH:
                formatted_text = formatted_text[:config.MAX_MESSAGE_LENGTH - 3] + "..."

            sent_message = None

            if media_info:
                logger.debug(
                    f"🔍 DEBUG: media_info: type={media_info.get('type')}, filename={media_info.get('filename')}, size={len(media_info.get('data', b''))} bytes")
            else:
                logger.debug("🔍 DEBUG: media_info=None")

            # Отправляем медиа или текст в конкретный тред
            if media_info:
                logger.debug(f"🔍 DEBUG: Отправка медиа")
                try:
                    sent_message = await asyncio.wait_for(
                        self._send_media_to_thread(formatted_text, media_info, thread_id),
                        timeout=60.0
                    )
                except asyncio.TimeoutError:
                    logger.error("❌ Таймаут отправки медиа (60s)")
                    if text:
                        sent_message = await self._send_text_to_thread(
                            f"[{source}] {username}: {text}\n\n⚠️ (медиафайл не отправлен - таймаут)",
                            thread_id
                        )
            elif text:
                logger.debug(f"🔍 DEBUG: Отправка текста в тред {thread_id}")
                sent_message = await self._send_text_to_thread(formatted_text, thread_id)
                logger.debug(
                    f"🔍 DEBUG: Текст отправлен, message_id={sent_message.message_id if sent_message else 'None'}")

            # Логируем в БД
            if sent_message:
                logger.debug(f"🔍 DEBUG: Логирование в БД")
                await db.log_message(
                    source=source.lower(),
                    destination='telegram',
                    source_id=source_message_id or 'unknown',
                    destination_id=str(sent_message.message_id),
                    username=username,
                    content=text,
                    media_type=media_info['type'] if media_info else None,
                    thread_id=thread_id,
                    vk_peer_id=vk_peer_id
                )

                logger.info(f"✅ {source} → TG (thread {thread_id}): {username}")
            else:
                logger.warning(f"⚠️ sent_message = None!")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки в Telegram тред: {e}", exc_info=True)
            await db.log_error(f"telegram_send_thread_{source.lower()}", str(e))

    async def _send_text_to_thread(self, text: str, thread_id: int):
        """Отправка текста в конкретный тред"""
        try:
            # КРИТИЧЕСКАЯ ПРОВЕРКА
            if not self.bot:
                logger.error("❌ FATAL: self.bot = None!")
                raise Exception("Telegram bot не инициализирован")

            if not self.application:
                logger.error("❌ FATAL: self.application = None!")
                raise Exception("Telegram application не инициализирован")

            logger.debug(f"🔍 _send_text_to_thread: chat={config.TELEGRAM_CHAT_ID}, thread={thread_id}")
            logger.debug(f"🔍 self.bot type: {type(self.bot)}")
            logger.debug(f"🔍 text: {text[:100]}")

            result = await self.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=text,
                message_thread_id=thread_id
            )

            logger.debug(f"✅ _send_text_to_thread SUCCESS: msg_id={result.message_id}")
            return result

        except Exception as e:
            logger.error(f"❌ _send_text_to_thread EXCEPTION: {type(e).__name__}: {e}", exc_info=True)
            raise

    async def _send_media_to_thread(self, caption: str, media_info: dict, thread_id: int):
        """Отправка медиа в конкретный тред"""
        media_type = media_info['type']
        file_data = media_info['data']
        filename = media_info['filename']

        # Создаём BytesIO объект
        file_obj = io.BytesIO(file_data)
        file_obj.name = filename

        # Отправляем в зависимости от типа
        if media_type == 'image':
            return await self.bot.send_photo(
                chat_id=config.TELEGRAM_CHAT_ID,
                photo=file_obj,
                caption=caption,
                message_thread_id=thread_id
            )
        elif media_type == 'video':
            return await self.bot.send_video(
                chat_id=config.TELEGRAM_CHAT_ID,
                video=file_obj,
                caption=caption,
                message_thread_id=thread_id
            )
        elif media_type == 'audio':
            return await self.bot.send_audio(
                chat_id=config.TELEGRAM_CHAT_ID,
                audio=file_obj,
                caption=caption,
                message_thread_id=thread_id
            )
        else:  # document
            return await self.bot.send_document(
                chat_id=config.TELEGRAM_CHAT_ID,
                document=file_obj,
                caption=caption,
                message_thread_id=thread_id
            )

    async def start(self):
        """Запуск бота"""
        self.is_running = True
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)

    async def stop(self):
        """Остановка бота"""
        logger.info("🛑 Останавливаю Telegram бота...")
        self.is_running = False

        if self.application:
            try:
                # Останавливаем updater
                if self.application.updater and self.application.updater.running:
                    await self.application.updater.stop()

                # Останавливаем приложение
                if self.application.running:
                    await self.application.stop()

                # Завершаем
                await self.application.shutdown()

                logger.info("✅ Telegram бот остановлен")
            except Exception as e:
                logger.error(f"❌ Ошибка остановки Telegram: {e}")
