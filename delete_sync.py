"""
Модуль синхронизации удалений между Telegram и VK.

Логика:
- Периодически проверяет сообщения за последние 7 дней из БД
- Для TG: setMessageReaction с пустым списком → если ошибка "not found" — удалено → удаляем парное в VK
- Для VK: messages.getById батчами → если нет в ответе — удалено → удаляем парное в TG

Адаптивная проверка TG (VK всегда батчами — быстро):
- Свежие (0-24ч): каждый цикл (5 мин) — ~200 msg, ~3.5 мин
- Средние (1-3 дня): каждый 6-й цикл (~30 мин)
- Старые (3-7 дней): каждый 36-й цикл (~3 часа)

Лимиты:
- Telegram: 30 req/s глобально, ~20 req/min в один чат → 1 req/s для проверок
- VK: 20 req/s → батч до 100 id за запрос, 3 req/s для проверок
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from logger import get_logger
from config import config
from database import db

logger = get_logger(__name__)


class DeleteSyncManager:
    """Менеджер синхронизации удалений между TG и VK"""

    def __init__(self, telegram_handler, vk_handler):
        self.telegram_handler = telegram_handler
        self.vk_handler = vk_handler
        self.is_running = False

        # Настройки
        self.check_interval = 300  # 5 минут между циклами
        self.days_to_check = 7
        self.tg_delay = 3.0  # Задержка между проверками TG (1 req/s)
        self.vk_batch_size = 100  # VK позволяет до 100 id за запрос
        self.vk_delay = 0.35  # Задержка между батчами VK (~3 req/s)

        # Адаптивные уровни проверки TG
        # (название, начало периода, конец периода, частота в циклах)
        # Частота 1 = каждый цикл, 6 = каждый 6-й, 36 = каждый 36-й
        self.tg_tiers = [
            ("свежие 0-24ч", timedelta(hours=24), timedelta(0), 1),
            ("средние 1-3д", timedelta(days=3), timedelta(hours=24), 6),
            ("старые 3-7д", timedelta(days=7), timedelta(days=3), 36),
        ]

        # Счётчик циклов для адаптивной проверки
        self.cycle_count = 0

        # Статистика
        self.tg_deletions = 0
        self.vk_deletions = 0

    async def start(self):
        """Запуск фонового сканирования"""
        self.is_running = True
        logger.info("🔄 DeleteSync запущен: проверка каждые "
                     f"{self.check_interval}s, глубина {self.days_to_check} дней")

        # Ждём 60 секунд перед первым циклом (дать ботам запуститься)
        await asyncio.sleep(60)

        while self.is_running:
            try:
                await self._run_check_cycle()
            except Exception as e:
                logger.error(f"❌ DeleteSync ошибка цикла: {e}", exc_info=True)

            # Ждём до следующего цикла (прерываемо)
            for _ in range(self.check_interval):
                if not self.is_running:
                    break
                await asyncio.sleep(1)

    async def stop(self):
        """Остановка"""
        self.is_running = False
        logger.info(f"🛑 DeleteSync остановлен. "
                     f"Удалено за сессию: TG→VK={self.tg_deletions}, VK→TG={self.vk_deletions}")

    async def _run_check_cycle(self):
        """Один цикл проверки с адаптивными уровнями для TG"""
        start_time = time.time()
        self.cycle_count += 1

        now = datetime.now(timezone.utc)

        # TG: адаптивная проверка по уровням
        tg_deleted = 0
        for tier_name, age_from, age_to, frequency in self.tg_tiers:
            if self.cycle_count % frequency != 0:
                continue

            since = now - age_from
            until = now - age_to

            tier_deleted = await self._check_telegram_deletions(since, until)
            tg_deleted += tier_deleted

            if tier_deleted:
                logger.info(f"🔄 DeleteSync TG [{tier_name}]: удалено {tier_deleted}")

        # VK: всегда полная проверка (батчами — быстро)
        vk_since = now - timedelta(days=self.days_to_check)
        vk_deleted = await self._check_vk_deletions(vk_since)

        elapsed = time.time() - start_time
        if tg_deleted or vk_deleted:
            logger.info(f"🔄 DeleteSync цикл #{self.cycle_count} за {elapsed:.1f}s. "
                        f"Удалено: TG→VK={tg_deleted}, VK→TG={vk_deleted}")
        else:
            logger.debug(f"🔄 DeleteSync цикл #{self.cycle_count} за {elapsed:.1f}s, удалений нет")

    # ──────────────────────────────────────────────
    # Проверка удалений в Telegram
    # ──────────────────────────────────────────────

    async def _check_telegram_deletions(self, since: datetime,
                                         until: datetime) -> int:
        """Проверить удалённые сообщения в Telegram за период, удалить парные в VK"""
        deleted_count = 0

        try:
            # Получаем сообщения TG→VK за период [since, until]
            messages = await db.get_synced_messages(
                source='telegram',
                destination='vk',
                since=since,
                until=until
            )

            if not messages:
                return 0

            logger.debug(f"🔍 DeleteSync TG: проверка {len(messages)} сообщений")

            for msg in messages:
                if not self.is_running:
                    break

                tg_message_id = msg['source_id']
                vk_message_id = msg['destination_id']

                # Проверяем существует ли сообщение в TG
                exists = await self._check_tg_message_exists(int(tg_message_id))

                if not exists:
                    # Удалено в TG → удаляем в VK
                    logger.info(f"🗑️ TG msg {tg_message_id} удалено → удаляю VK msg {vk_message_id}")
                    success = await self._delete_vk_message(int(vk_message_id))

                    if success:
                        deleted_count += 1
                        self.tg_deletions += 1

                    # Убираем из БД чтобы не проверять повторно
                    await db.delete_synced_message(
                        source='telegram',
                        source_id=tg_message_id
                    )

                # Соблюдаем лимит
                await asyncio.sleep(self.tg_delay)

        except Exception as e:
            logger.error(f"❌ DeleteSync TG check error: {e}", exc_info=True)

        return deleted_count

    async def _check_tg_message_exists(self, message_id: int) -> bool:
        """
        Проверить существует ли сообщение в Telegram.

        Способ: setMessageReaction с пустым списком реакций.
        - Если сообщение существует → True (ничего не произойдёт, реакции бота и так нет)
        - Если удалено → ошибка "message to react not found" → False
        - Никаких побочных эффектов.
        """
        try:
            await self.telegram_handler.bot.set_message_reaction(
                chat_id=config.TELEGRAM_CHAT_ID,
                message_id=message_id,
                reaction=[]  # Пустой список = снять реакцию (которой и так нет)
            )
            # Успех → сообщение существует
            return True

        except Exception as e:
            error_str = str(e).lower()
            if ('message to react not found' in error_str or
                    'message not found' in error_str or
                    'message_id_invalid' in error_str):
                return False

            if 'reaction_empty' in error_str:
                return True

            # Другая ошибка — считаем что существует (безопасный вариант)
            logger.warning(f"⚠️ DeleteSync: ошибка проверки TG msg {message_id}: {e}")
            return True

    # ──────────────────────────────────────────────
    # Проверка удалений в VK
    # ──────────────────────────────────────────────

    async def _check_vk_deletions(self, since: datetime) -> int:
        """Проверить удалённые сообщения в VK, удалить парные в Telegram"""
        deleted_count = 0

        try:
            # Получаем сообщения VK→TG за период
            # source='vk' означает что source_id — это VK message_id
            messages = await db.get_synced_messages(
                source='vk',
                destination='telegram',
                since=since
            )

            if not messages:
                return 0

            logger.debug(f"🔍 DeleteSync VK: проверка {len(messages)} сообщений")

            # Разбиваем на батчи по vk_batch_size
            for i in range(0, len(messages), self.vk_batch_size):
                if not self.is_running:
                    break

                batch = messages[i:i + self.vk_batch_size]
                batch_deleted = await self._check_vk_batch(batch)
                deleted_count += batch_deleted

                # Соблюдаем лимит VK
                await asyncio.sleep(self.vk_delay)

        except Exception as e:
            logger.error(f"❌ DeleteSync VK check error: {e}", exc_info=True)

        return deleted_count

    async def _check_vk_batch(self, messages: list) -> int:
        """Проверить батч сообщений VK через messages.getById"""
        deleted_count = 0

        try:
            vk_ids = [int(msg['source_id']) for msg in messages]

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.vk_handler.vk_session.method('messages.getById', {
                    'message_ids': ','.join(str(i) for i in vk_ids),
                    'group_id': config.VK_GROUP_ID
                })
            )

            # Собираем существующие id
            existing_ids = set()
            if response and 'items' in response:
                for item in response['items']:
                    if not item.get('deleted') and not item.get('is_unavailable'):
                        existing_ids.add(item['id'])

            # Находим удалённые
            for msg in messages:
                vk_id = int(msg['source_id'])
                tg_message_id = msg['destination_id']

                if vk_id not in existing_ids:
                    logger.info(f"🗑️ VK msg {vk_id} удалено → удаляю TG msg {tg_message_id}")

                    success = await self._delete_tg_message(int(tg_message_id))

                    if success:
                        deleted_count += 1
                        self.vk_deletions += 1

                    await db.delete_synced_message(
                        source='vk',
                        source_id=str(vk_id)
                    )

                    await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"❌ DeleteSync VK batch error: {e}", exc_info=True)

        return deleted_count

    # ──────────────────────────────────────────────
    # Удаление сообщений
    # ──────────────────────────────────────────────

    async def _delete_vk_message(self, message_id: int) -> bool:
        """Удалить сообщение в VK"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.vk_handler.vk_api.messages.delete(
                    message_ids=[message_id],
                    delete_for_all=1
                )
            )
            logger.info(f"✅ VK msg {message_id} удалено")
            return True
        except Exception as e:
            logger.error(f"❌ Не удалось удалить VK msg {message_id}: {e}")
            return False

    async def _delete_tg_message(self, message_id: int) -> bool:
        """Удалить сообщение в Telegram"""
        try:
            await self.telegram_handler.bot.delete_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                message_id=message_id
            )
            logger.info(f"✅ TG msg {message_id} удалено")
            return True
        except Exception as e:
            error_str = str(e).lower()
            if 'message to delete not found' in error_str:
                logger.debug(f"TG msg {message_id} уже удалено")
                return True
            logger.error(f"❌ Не удалось удалить TG msg {message_id}: {e}")
            return False
