"""
Очередь сообщений для предотвращения rate limits
"""
import asyncio
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Any
from logger import get_logger
from config import config

logger = get_logger(__name__)


class MessageQueue:
    """Очередь с rate limiting"""

    def __init__(self):
        self.queue = deque()
        self.timestamps = deque()
        self.processing = False

    async def add(self, func: Callable, *args, **kwargs):
        """Добавить задачу в очередь"""
        self.queue.append((func, args, kwargs))

        if not self.processing:
            asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        """Обработка очереди с учётом rate limits"""
        self.processing = True

        while self.queue:
            # Очищаем старые timestamps
            now = datetime.now()
            cutoff = now - timedelta(seconds=config.RATE_LIMIT_PERIOD)

            while self.timestamps and self.timestamps[0] < cutoff:
                self.timestamps.popleft()

            # Проверяем rate limit
            if len(self.timestamps) >= config.RATE_LIMIT_MESSAGES:
                wait_time = (self.timestamps[0] + timedelta(
                    seconds=config.RATE_LIMIT_PERIOD
                ) - now).total_seconds()

                if wait_time > 0:
                    logger.warning(f"⏳ Rate limit достигнут, ожидание {wait_time:.1f}s")
                    await asyncio.sleep(wait_time)
                    continue

            # Обрабатываем следующее сообщение
            func, args, kwargs = self.queue.popleft()

            try:
                if asyncio.iscoroutinefunction(func):
                    await func(*args, **kwargs)
                else:
                    func(*args, **kwargs)

                self.timestamps.append(datetime.now())

            except Exception as e:
                logger.error(f"❌ Ошибка обработки задачи из очереди: {e}")

            # Небольшая задержка между сообщениями
            await asyncio.sleep(0.5)

        self.processing = False


# Глобальные очереди
telegram_queue = MessageQueue()
discord_queue = MessageQueue()