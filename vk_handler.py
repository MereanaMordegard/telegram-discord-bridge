"""
Обработчик VK сообщений
"""
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id
import asyncio
import io
import html
import requests
import json
import os
from typing import Optional
from logger import get_logger
from config import config
from database import db
from media_handler import media_handler
from message_queue import MessageQueue
from message_queue import telegram_queue
from vk_thread_mapping import (
    get_telegram_thread_id,
    get_thread_name,
    is_thread_synced
)
from vk_thread_mapping import get_vk_peer_id as _get_vk_peer_id

CATCH_UP_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "vk_catchup_state.json"
)

logger = get_logger(__name__)


class VKHandler:
    """Класс для обработки VK событий"""

    def __init__(self, telegram_sender):
        self.vk_session = None
        self.vk_api = None
        self.longpoll = None
        self.telegram_sender = telegram_sender
        self.is_running = False
        self.queue = MessageQueue()
        self.processed_ids = set()
        self.max_processed_ids = 5000
        self._heartbeat = "__heartbeat__"
        self._catchup_state = self._load_catchup_state()

    # ─── Catch-up state persistence ──────────────────────────────────

    def _load_catchup_state(self) -> dict:
        """Загрузка { "<peer_id>": <last_conversation_message_id> }"""
        try:
            if os.path.exists(CATCH_UP_STATE_FILE):
                with open(CATCH_UP_STATE_FILE, "r") as f:
                    state = json.load(f)
                logger.info(f"📂 Catch-up state загружен: {len(state)} бесед")
                return state
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки catch-up state: {e}")
        return {}

    def _save_catchup_state(self):
        tmp = CATCH_UP_STATE_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self._catchup_state, f)
            os.replace(tmp, CATCH_UP_STATE_FILE)
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения catch-up state: {e}")

    def _update_catchup_id(self, peer_id: int, cmid: int):
        key = str(peer_id)
        if cmid > self._catchup_state.get(key, 0):
            self._catchup_state[key] = cmid
            self._save_catchup_state()

    # ─── Catch-up: догнать пропущенные сообщения ─────────────────────

    async def _catch_up_missed_messages(self):
        from vk_thread_mapping import VK_TO_THREAD

        peer_ids = set(VK_TO_THREAD.keys())

        if not peer_ids:
            return

        total = 0
        for peer_id in peer_ids:
            try:
                last_cmid = self._catchup_state.get(str(peer_id), 0)
                if last_cmid == 0:
                    await self._init_catchup_position(peer_id)
                    continue
                total += await self._fetch_and_forward_missed(peer_id, last_cmid)
            except Exception as e:
                logger.error(f"❌ Catch-up peer_id={peer_id}: {e}", exc_info=True)

        logger.info(f"✅ Catch-up завершён: переслано {total} сообщений")

    async def _init_catchup_position(self, peer_id: int):
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.vk_api.messages.getHistory(peer_id=peer_id, count=1)
        )
        items = result.get("items", [])
        if items:
            cmid = items[0].get("conversation_message_id", items[0]["id"])
            self._update_catchup_id(peer_id, cmid)
            logger.info(f"📌 Catch-up init peer_id={peer_id}: last_cmid={cmid}")

    async def _fetch_and_forward_missed(self, peer_id: int, last_cmid: int) -> int:
        loop = asyncio.get_event_loop()
        MAX_FETCH = 200
        all_missed = []
        offset = 0

        while offset < MAX_FETCH:
            result = await loop.run_in_executor(
                None,
                lambda off=offset: self.vk_api.messages.getHistory(
                    peer_id=peer_id, count=min(200, MAX_FETCH - offset), offset=off
                )
            )
            items = result.get("items", [])
            if not items:
                break

            found_boundary = False
            for msg in items:
                cmid = msg.get("conversation_message_id", msg["id"])
                if cmid <= last_cmid:
                    found_boundary = True
                    break
                all_missed.append(msg)

            if found_boundary:
                break
            offset += len(items)
            await asyncio.sleep(0.35)

        if not all_missed:
            return 0

        all_missed.reverse()  # хронологический порядок

        thread_id = get_telegram_thread_id(peer_id)
        if not thread_id:
            return 0

        logger.info(f"📨 Catch-up peer_id={peer_id}: {len(all_missed)} пропущенных")

        forwarded = 0
        for msg in all_missed:
            try:
                from_id = msg.get("from_id", 0)
                cmid = msg.get("conversation_message_id", msg["id"])
                dedup_key = f"{peer_id}_{cmid}"

                if from_id < 0 or dedup_key in self.processed_ids:
                    text = msg.get("text", "")
                    if text.startswith("[TG] "):
                        self._update_catchup_id(peer_id, cmid)
                        continue

                await self._forward_catchup_message(msg, peer_id, thread_id)
                self.processed_ids.add(dedup_key)
                self._update_catchup_id(peer_id, cmid)
                forwarded += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"❌ Catch-up forward: {e}", exc_info=True)
                break

        return forwarded

    async def _forward_catchup_message(self, msg: dict, peer_id: int, thread_id: int):
        from_id = msg.get("from_id", 0)
        text = msg.get("text", "")
        username = await self._get_username(from_id)

        # Reply обработка
        if msg.get("reply_message"):
            reply = msg["reply_message"]
            reply_from_id = reply.get("from_id", 0)
            reply_user = await self._get_username(reply_from_id) if reply_from_id > 0 else "Бот"
            reply_text = reply.get("text", "") or "сообщение"
            if len(reply_text) > 200:
                reply_text = reply_text[:200] + "..."
            import re
            reply_text = re.sub(r'💬\s*.+?:\s*«.*?»\s*\n*', '', reply_text, flags=re.DOTALL).strip()
            reply_text = re.sub(r'^\[(?:TG|VK|Discord)\]\s*[^:]+:\s*', '', reply_text).strip()
            if not reply_text:
                reply_text = "сообщение"
            text = html.escape(text) if text else ""
            text = f"💬 {html.escape(reply_user)}:\n<blockquote>{html.escape(reply_text)}</blockquote>\n{text}"

        # Фильтр фото-треда
        PHOTO_THREAD_ID = 14101
        if thread_id == PHOTO_THREAD_ID:
            attachments = msg.get("attachments", [])
            if not any(a["type"] in ("photo", "video") for a in attachments):
                return

        # Вложения
        media_info = await self._process_attachments(msg)
        if media_info and media_info.get("type") in ("text_extra", "video_link"):
            extra = media_info.get("text") or ""
            if media_info.get("type") == "video_link":
                extra = f"🎬 {media_info['title']}\n{media_info['url']}"
            text = f"{text}\n\n{extra}" if text else extra
            media_info = None

        cmid = msg.get("conversation_message_id", msg["id"])
        await telegram_queue.add(
            self.telegram_sender.send_message_to_thread,
            text=text, username=username, thread_id=thread_id,
            media_info=media_info, source="VK",
            source_message_id=str(cmid), vk_peer_id=peer_id,
        )
        logger.info(f"📨 Catch-up VK→TG ({get_thread_name(thread_id)}): {username}: {(text or '')[:50]}")

    async def initialize(self):
        """Инициализация VK бота"""
        try:
            # Авторизация
            self.vk_session = vk_api.VkApi(token=config.VK_ACCESS_TOKEN)
            self.vk_api = self.vk_session.get_api()

            # Long Poll для получения событий с таймаутом
            self.longpoll = VkBotLongPoll(
                self.vk_session,
                config.VK_GROUP_ID,
                wait=25  # Таймаут ожидания событий (секунды)
            )

            logger.info(f"✅ VK бот инициализирован для группы {config.VK_GROUP_ID}")

        except Exception as e:
            logger.error(f"❌ Ошибка инициализации VK: {e}")
            raise

    async def start(self):
        """Запуск бота с автоматическим перезапуском Long Poll"""
        self.is_running = True
        logger.info("🚀 VK бот запущен (ASYNC), слушаю события...")

        try:
            await self._catch_up_missed_messages()
        except Exception as e:
            logger.error(f"❌ Catch-up при старте: {e}", exc_info=True)

        reconnect_delay = 5

        while self.is_running:
            try:
                await self._listen_events()
            except Exception as e:
                logger.error(f"❌ Long Poll упал: {e}", exc_info=True)

            if not self.is_running:
                break

            logger.warning(f"🔄 Переподключение VK Long Poll через {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)

            try:
                self.longpoll = VkBotLongPoll(
                    self.vk_session, config.VK_GROUP_ID, wait=25
                )
                logger.info("✅ VK Long Poll переинициализирован")
                reconnect_delay = 5

                try:
                    await self._catch_up_missed_messages()
                except Exception as e:
                    logger.error(f"❌ Catch-up после реконнекта: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"❌ Ошибка переинициализации: {e}", exc_info=True)

        logger.info("🛑 VK бот остановлен")

    async def _listen_events(self):
        """Слушать события VK через отдельный поток"""
        logger.info("🔍 VK Long Poll: начинаю слушать события...")

        event_queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _poll_thread():
            """Блокирующий поток: слушает VK и кладёт события в очередь"""
            try:
                while self.is_running:
                    try:
                        for event in self.longpoll.check():
                            if not self.is_running:
                                return
                            loop.call_soon_threadsafe(event_queue.put_nowait, event)
                        # После каждого check — heartbeat
                        loop.call_soon_threadsafe(event_queue.put_nowait, "__heartbeat__")
                    except Exception as e:
                        loop.call_soon_threadsafe(event_queue.put_nowait, e)
                        return
            except Exception as e:
                loop.call_soon_threadsafe(event_queue.put_nowait, e)

        import threading
        poll_thread = threading.Thread(target=_poll_thread, daemon=True)
        poll_thread.start()

        while self.is_running:
            try:
                item = await asyncio.wait_for(event_queue.get(), timeout=60)

                if item == "__heartbeat__":
                    continue

                if isinstance(item, Exception):
                    raise item

                event = item
                logger.debug(f"🔔 Событие: {event.type}")

                if event.type == VkBotEventType.MESSAGE_NEW:
                    try:
                        message = event.object.message
                        logger.debug(
                            f"📨 peer_id={message['peer_id']}, text={message.get('text', '')[:50]}")
                        await self._handle_message(event)
                        logger.info("🔍 _listen_events: вернулся из _handle_message")
                    except Exception as e:
                        logger.error(f"❌ Ошибка обработки события: {e}", exc_info=True)

            except asyncio.TimeoutError:
                logger.warning("⏰ VK Long Poll: нет heartbeat 60s — переподключение")
                raise
            except Exception as e:
                logger.error(f"❌ Ошибка в цикле Long Poll: {e}", exc_info=True)
                raise

    async def post_to_wall(
            self,
            text: str,
            username: str,
            media_info: Optional[dict] = None,
            telegram_message_id: Optional[str] = None
    ):
        """Публикация на стену сообщества VK"""
        try:
            formatted_text = text or ""

            params = {
                'owner_id': -config.VK_GROUP_ID,
                'from_group': 1,
                'message': formatted_text
            }

            # Если есть фото — загружаем на стену
            if media_info and media_info['type'] == 'image':
                attachment = await self._upload_wall_photo(media_info)
                if attachment:
                    params['attachments'] = attachment

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.vk_api.wall.post(**params)
            )

            if response:
                logger.info(f"✅ TG → VK стена: {username}")
                await db.log_message(
                    source='telegram',
                    destination='vk_wall',
                    source_id=telegram_message_id or 'unknown',
                    destination_id=str(response.get('post_id', '')),
                    username=username,
                    content=text,
                    media_type=media_info['type'] if media_info else None
                )

        except Exception as e:
            logger.error(f"❌ Ошибка публикации на стену VK: {e}", exc_info=True)
            await db.log_error("vk_wall_post", str(e))

    def _upload_wall_photo_sync(self, file_obj) -> Optional[str]:
        """Синхронная загрузка фото на стену"""
        try:
            import requests

            upload_data = self.vk_api.photos.getWallUploadServer(
                group_id=config.VK_GROUP_ID
            )
            upload_url = upload_data['upload_url']

            file_obj.seek(0)
            files = {'photo': (file_obj.name, file_obj, 'image/jpeg')}
            response = requests.post(upload_url, files=files)
            upload_result = response.json()

            photos = self.vk_api.photos.saveWallPhoto(
                group_id=config.VK_GROUP_ID,
                photo=upload_result['photo'],
                server=upload_result['server'],
                hash=upload_result['hash']
            )

            if photos:
                photo = photos[0]
                return f"photo{photo['owner_id']}_{photo['id']}"

            return None

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки фото на стену: {e}", exc_info=True)
            return None

    async def _upload_wall_photo(self, media_info: dict) -> Optional[str]:
        """Загрузка фото на стену"""
        try:
            file_obj = io.BytesIO(media_info['data'])
            file_obj.name = media_info['filename']

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._upload_wall_photo_sync,
                file_obj
            )
            return result

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки фото на стену: {e}", exc_info=True)
            return None

    def _get_longpoll_events(self):
        """Получить события VK Long Poll (синхронный метод)"""
        try:
            # Получаем события с таймаутом
            events = []
            for event in self.longpoll.check():
                events.append(event)
                # Ограничиваем количество событий за раз
                if len(events) >= 10:
                    break
            return events
        except Exception as e:
            logger.error(f"❌ Ошибка получения событий: {e}")
            return []

    async def _handle_message(self, event):
        """Обработка входящего сообщения из VK"""
        try:
            logger.info(f"🔍 _handle_message START: msg_id={event.object.message['id']}")
            message = event.object.message

            # Дедупликация — защита от повторных событий Long Poll
            msg_id = f"{message['peer_id']}_{message.get('conversation_message_id', message['id'])}"
            if msg_id in self.processed_ids:
                logger.debug(f"⏭️ Пропуск дубликата VK msg {msg_id}")
                return
            self.processed_ids.add(msg_id)
            if len(self.processed_ids) > self.max_processed_ids:
                # Убираем самые старые (примерно половину)
                to_remove = sorted(self.processed_ids)[:self.max_processed_ids // 2]
                self.processed_ids -= set(to_remove)

            peer_id = message['peer_id']
            from_id = message['from_id']
            text = message.get('text', '')

            # Ответ на сообщение (цитата)
            if message.get('reply_message'):
                reply = message['reply_message']
                reply_from_id = reply.get('from_id', 0)
                reply_user = await self._get_username(reply_from_id) if reply_from_id > 0 else "Бот"
                reply_text = reply.get('text', '')

                if not reply_text:
                    reply_attachments = reply.get('attachments', [])
                    if reply_attachments:
                        attach_type = reply_attachments[0]['type']
                        if attach_type == 'photo':
                            reply_text = "🖼 Фото"
                        elif attach_type == 'video':
                            reply_text = "🎬 Видео"
                        elif attach_type == 'doc':
                            doc_title = reply_attachments[0]['doc'].get('title', 'без имени')
                            if doc_title.endswith('.gif'):
                                reply_text = "🎞 GIF"
                            else:
                                reply_text = f"📎 Файл: {doc_title}"
                        elif attach_type == 'audio_message':
                            reply_text = "🎤 Голосовое сообщение"
                        elif attach_type == 'audio':
                            reply_text = "🎵 Аудио"
                        elif attach_type == 'sticker':
                            reply_text = "🏷 Стикер"
                        elif attach_type == 'poll':
                            question = reply_attachments[0]['poll'].get('question', 'Опрос')
                            reply_text = f"📊 Опрос: {question}"
                        elif attach_type == 'geo':
                            reply_text = "📍 Геопозиция"
                        else:
                            reply_text = "сообщение"
                    else:
                        reply_text = "сообщение"
                else:
                    if len(reply_text) > 200:
                        reply_text = reply_text[:200] + "..."
                    reply_attachments = reply.get('attachments', [])
                    if reply_attachments:
                        attach_type = reply_attachments[0]['type']
                        if attach_type == 'photo':
                            reply_text = f"🖼 {reply_text}"
                        elif attach_type == 'video':
                            reply_text = f"🎬 {reply_text}"
                        elif attach_type == 'doc':
                            reply_text = f"📎 {reply_text}"

                import re
                # Убираем блок "💬 Имя: «текст»\n\n" (старый формат кавычек)
                reply_text = re.sub(r'💬\s*.+?:\s*«.*?»\s*\n*', '', reply_text, flags=re.DOTALL).strip()
                # Убираем префикс "[TG] Имя:" или "[VK] Имя:"
                reply_text = re.sub(r'^\[(?:TG|VK|Discord)\]\s*[^:]+:\s*', '', reply_text).strip()

                if not reply_text:
                    reply_text = "сообщение"

                text = html.escape(text)
                text = f"💬 {html.escape(reply_user)}:\n<blockquote>{html.escape(reply_text)}</blockquote>\n{text}"

            # Получаем соответствующий Telegram thread
            thread_id = get_telegram_thread_id(peer_id)

            # Фильтрация в треде "Фотографии" — пропускаем всё кроме фото/видео
            PHOTO_THREAD_ID = 14101

            if thread_id == PHOTO_THREAD_ID:
                attachments = message.get('attachments', [])
                has_photo_or_video = any(
                    a['type'] in ('photo', 'video') for a in attachments
                )
                if not has_photo_or_video:
                    # Удаляем сообщение из VK беседы
                    try:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None,
                            lambda: self.vk_api.messages.delete(
                                cmids=message["conversation_message_id"],
                                delete_for_all=1,
                                peer_id=peer_id
                            )
                        )
                        username_for_log = await self._get_username(from_id) if from_id > 0 else "Бот"
                        logger.info(f"🗑 Удалено не-фото/видео из VK Фотографии: {username_for_log}: {text[:50]}")
                    except Exception as e:
                        logger.warning(f"⚠️ Не удалось удалить VK сообщение из Фотографии: {e}")
                    return  # НЕ пересылаем в Telegram

            if not thread_id:
                logger.debug(f"⏭️ Пропуск VK беседы {peer_id}: нет маппинга")
                return

            # Игнорируем сообщения от бота (от самого сообщества)
            if from_id < 0:
                logger.debug("⏭️ Пропуск: сообщение от бота")
                return

            # Получаем информацию об отправителе
            username = await self._get_username(from_id)

            # Обрабатываем вложения (фото, видео и т.д.)
            media_info = await self._process_attachments(message)

            # Если это текстовое дополнение (гео, опрос, видео-ссылка)
            if media_info and media_info.get('type') in ('text_extra', 'video_link'):
                extra_text = media_info.get('text') or media_info.get('url', '')
                if media_info.get('type') == 'video_link':
                    extra_text = f"🎬 {media_info['title']}\n{media_info['url']}"
                text = f"{text}\n\n{extra_text}" if text else extra_text
                media_info = None

            if media_info and media_info.get('type') == 'video_link':
                video_title = media_info['title']
                video_url = media_info['url']
                text = f"{text}\n\n🎬 {video_title}\n{video_url}" if text else f"🎬 {video_title}\n{video_url}"
                media_info = None  # Убираем медиа, отправляем как текст

            # Отправляем в Telegram через очередь
            await telegram_queue.add(
                self.telegram_sender.send_message_to_thread,
                text=text,
                username=username,
                thread_id=thread_id,
                media_info=media_info,
                source='VK',
                source_message_id=str(message.get('conversation_message_id', message['id'])),
                vk_peer_id=peer_id
            )

            thread_name = get_thread_name(thread_id)
            logger.info(
                f"📨 VK ({thread_name}) → TG: {username}: "
                f"{text[:50]}{'...' if len(text) > 50 else ''}"
            )
            logger.info(f"🔍 _handle_message END: ok")

            cmid = message.get('conversation_message_id', message['id'])
            self._update_catchup_id(peer_id, cmid)

        except Exception as e:
            logger.error(f"❌ Ошибка обработки VK сообщения: {e}", exc_info=True)
            await db.log_error("vk_message_handler", str(e))

    async def _get_username(self, user_id: int) -> str:
        try:
            loop = asyncio.get_event_loop()
            users = await loop.run_in_executor(
                None,
                lambda: self.vk_api.users.get(user_ids=[user_id])
            )
            if users:
                user = users[0]
                return f"{user['first_name']} {user['last_name']}"
            return f"User{user_id}"
        except Exception as e:
            logger.error(f"❌ Ошибка получения имени VK: {e}")
            return f"User{user_id}"

    async def _process_attachments(self, message) -> Optional[dict]:
        """Обработка вложений из VK"""
        try:
            attachments = message.get('attachments', [])

            if not attachments:
                return None

            # Берём первое вложение
            attachment = attachments[0]
            attach_type = attachment['type']

            # Обрабатываем фото
            if attach_type == 'photo':
                photo = attachment['photo']
                # Берём максимальное разрешение
                sizes = photo['sizes']
                max_size = max(sizes, key=lambda x: x['width'] * x['height'])
                url = max_size['url']

                # Скачиваем
                file_data = await media_handler.download_file(url)
                if file_data:
                    return {
                        'type': 'image',
                        'data': file_data,
                        'filename': f"vk_photo_{photo['id']}.jpg"
                    }

            elif attach_type == 'video':
                video = attachment['video']
                # VK не даёт прямую ссылку на видео файл через API
                # Отправляем ссылку на видео текстом
                owner_id = video['owner_id']
                video_id = video['id']
                video_title = video.get('title', 'Видео')
                video_url = f"https://vk.com/video{owner_id}_{video_id}"

                # Возвращаем как текстовое дополнение (не медиа)
                return {
                    'type': 'video_link',
                    'url': video_url,
                    'title': video_title
                }

            # Обрабатываем документы
            elif attach_type == 'doc':
                doc = attachment['doc']
                url = doc['url']

                file_data = await media_handler.download_file(url)
                if file_data:
                    file_type = media_handler.get_file_type(doc['title'])
                    return {
                        'type': file_type,
                        'data': file_data,
                        'filename': doc['title']
                    }

            # Геопозиция
            elif attach_type == 'geo':
                geo = attachment['geo']
                lat = geo['coordinates']['latitude']
                lon = geo['coordinates']['longitude']
                maps_link = f"https://www.google.com/maps?q={lat},{lon}"
                place_title = geo.get('place', {}).get('title', '')
                if place_title:
                    return {
                        'type': 'text_extra',
                        'text': f"📍 {place_title}\n{maps_link}"
                    }
                else:
                    return {
                        'type': 'text_extra',
                        'text': f"📍 Геопозиция: {maps_link}"
                    }

            # Опрос
            elif attach_type == 'poll':
                poll = attachment['poll']
                poll_text = f"📊 Опрос: {poll['question']}\n"
                for i, answer in enumerate(poll.get('answers', []), 1):
                    poll_text += f"  {i}. {answer['text']}\n"
                if poll.get('anonymous'):
                    poll_text += "(анонимный)"
                return {
                    'type': 'text_extra',
                    'text': poll_text
                }

            # Другие типы можно добавить позже

            return None

        except Exception as e:
            logger.error(f"❌ Ошибка обработки VK вложений: {e}")
            return None

    def _upload_video_sync(self, peer_id: int, file_obj, filename: str) -> Optional[str]:
        """Синхронная загрузка видео"""
        try:
            import requests

            logger.info(f"🔍 _upload_video_sync: peer_id={peer_id}, filename={filename}")

            # Получаем upload URL для видео
            upload_data = self.vk_api.video.save(
                name=filename,
                is_private=1,
                group_id=config.VK_GROUP_ID
            )
            upload_url = upload_data['upload_url']
            owner_id = upload_data['owner_id']
            video_id = upload_data['video_id']

            logger.info(f"🔍 upload_url: {upload_url[:80]}...")

            # Сбрасываем позицию
            file_obj.seek(0)

            # Загружаем файл
            files = {'video_file': (filename, file_obj)}
            response = requests.post(upload_url, files=files)
            upload_result = response.json()

            logger.info(f"🔍 Upload result: {upload_result}")

            logger.info(f"✅ Видео загружено: owner_id={owner_id}, id={video_id}")
            return f"video{owner_id}_{video_id}"

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки видео: {e}", exc_info=True)
            # Fallback — отправить как документ
            logger.info("🔄 Fallback: загрузка видео как документа")
            return self._upload_document_sync(peer_id, file_obj, filename)

    async def send_message(
            self,
            text: str,
            username: str,
            thread_id: int,
            media_info: Optional[dict] = None,
            telegram_message_id: Optional[str] = None
    ):
        """Отправка сообщения в VK беседу"""
        try:
            # Проверяем что тред синхронизируется
            if not is_thread_synced(thread_id):
                logger.debug(f"⏭️ Тред {thread_id} не синхронизируется с VK")
                return

            # Получаем VK peer_id
            from vk_thread_mapping import get_vk_peer_id
            peer_id = get_vk_peer_id(thread_id)

            if not peer_id:
                logger.error(f"❌ Нет VK peer_id для треда {thread_id}")
                return

            # Формируем текст
            formatted_text = f"[TG] {username}: {text}" if text else f"[TG] {username}:"

            # Отправляем сообщение
            params = {
                'peer_id': peer_id,
                'message': formatted_text,
                'random_id': get_random_id()
            }

            # Если есть медиа - загружаем и прикрепляем
            if media_info:
                attachment = await self._upload_media(peer_id, media_info)
                if attachment:
                    params['attachment'] = attachment

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.vk_api.messages.send(**params)
            )

            # Логируем в БД
            if response:
                await db.log_message(
                    source='telegram',
                    destination='vk',
                    source_id=telegram_message_id or 'unknown',
                    destination_id=str(response),
                    username=username,
                    content=text,
                    media_type=media_info['type'] if media_info else None,
                    thread_id=thread_id,
                    vk_peer_id=peer_id
                )

            thread_name = get_thread_name(thread_id)
            logger.info(f"✅ TG → VK ({thread_name}): {username}")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки в VK: {e}", exc_info=True)
            await db.log_error("vk_send", str(e))

    async def _upload_media(self, peer_id: int, media_info: dict) -> Optional[str]:
        """Загрузка медиа в VK"""
        try:
            logger.info(f"🔍 DEBUG _upload_media: peer_id={peer_id}, type={type(peer_id)}")

            # КРИТИЧЕСКАЯ ПРОВЕРКА
            if not isinstance(peer_id, int):
                logger.error(f"❌ peer_id не является int: {peer_id} ({type(peer_id)})")
                return None

            media_type = media_info['type']
            file_data = media_info['data']
            filename = media_info['filename']

            logger.info(f"📤 Загрузка {media_type} в VK: {filename}")

            # Создаём файл
            file_obj = io.BytesIO(file_data)
            file_obj.name = filename

            # Запускаем синхронный код в executor
            loop = asyncio.get_event_loop()

            if media_type == 'image':
                result = await loop.run_in_executor(
                    None,
                    self._upload_photo_sync,
                    peer_id,
                    file_obj
                )
                return result



            elif media_type in ['video', 'audio', 'document']:
                result = await loop.run_in_executor(
                    None,
                    self._upload_document_sync,
                    peer_id,
                    file_obj,
                    filename
                )
                return result

            return None

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки медиа в VK: {e}", exc_info=True)
            return None

    def _upload_photo_sync(self, peer_id: int, file_obj) -> Optional[str]:
        """Синхронная загрузка фото"""
        try:
            import requests

            logger.debug(f"🔍 _upload_photo_sync: peer_id={peer_id}")

            # Получаем upload URL
            upload_data = self.vk_api.photos.getMessagesUploadServer(peer_id=peer_id)
            upload_url = upload_data['upload_url']

            logger.debug(f"🔍 upload_url: {upload_url[:80]}...")

            # Сбрасываем позицию в файле
            file_obj.seek(0)

            # Загружаем файл через requests
            files = {'photo': (file_obj.name, file_obj, 'image/jpeg')}
            response = requests.post(upload_url, files=files)
            upload_result = response.json()

            logger.debug(f"🔍 Upload result: {upload_result}")

            # Сохраняем фото
            photos = self.vk_api.photos.saveMessagesPhoto(
                photo=upload_result['photo'],
                server=upload_result['server'],
                hash=upload_result['hash']
            )

            if photos:
                photo = photos[0]
                logger.info(f"✅ Фото сохранено: owner_id={photo['owner_id']}, id={photo['id']}")
                return f"photo{photo['owner_id']}_{photo['id']}"

            return None

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки фото: {e}", exc_info=True)
            return None

    def _upload_document_sync(self, peer_id: int, file_obj, filename: str) -> Optional[str]:
        """Синхронная загрузка документа"""
        try:
            import requests

            logger.debug(f"🔍 _upload_document_sync: peer_id={peer_id}, filename={filename}")

            # Получаем upload URL
            upload_data = self.vk_api.docs.getMessagesUploadServer(
                peer_id=peer_id,
                type='doc'
            )
            upload_url = upload_data['upload_url']

            logger.debug(f"🔍 upload_url: {upload_url[:80]}...")

            # Сбрасываем позицию
            file_obj.seek(0)

            # Загружаем файл
            files = {'file': (filename, file_obj)}
            response = requests.post(upload_url, files=files)
            upload_result = response.json()

            logger.debug(f"🔍 Upload result: {upload_result}")

            # Сохраняем документ
            docs = self.vk_api.docs.save(file=upload_result['file'], title=filename)

            if docs and 'doc' in docs:
                doc = docs['doc']
                logger.info(f"✅ Документ сохранён: owner_id={doc['owner_id']}, id={doc['id']}")
                return f"doc{doc['owner_id']}_{doc['id']}"

            return None

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки документа: {e}", exc_info=True)
            return None

    async def stop(self):
        """Остановка бота"""
        logger.info("🛑 Останавливаю VK бота...")
        self.is_running = False

        # Даём время Long Poll завершиться
        await asyncio.sleep(2)

        logger.info("✅ VK бот остановлен")