"""
Главный файл для запуска Telegram-Discord bridge
"""
import asyncio
import signal
import sys
from logger import BridgeLogger, get_logger
from config import config
import os
from database import db
from telegram_handler import TelegramHandler
from discord_handler import DiscordHandler
from vk_handler import VKHandler
from delete_sync import DeleteSyncManager

# Инициализация логирования
BridgeLogger()
logger = get_logger(__name__)

class BridgeBot:
    """Основной класс моста между Telegram и Discord"""

    def __init__(self):
        self.telegram_handler: TelegramHandler = None
        self.discord_handler: DiscordHandler = None
        self.vk_handler: VKHandler = None
        self.delete_sync: DeleteSyncManager = None
        self.is_running = False
        self.shutdown_event = asyncio.Event()

    async def initialize(self):
        """Инициализация всех компонентов"""
        try:
            logger.info("=" * 50)
            logger.info("🚀 ЗАПУСК TELEGRAM-DISCORD-VK BRIDGE")
            logger.info("=" * 50)

            # Показываем конфигурацию
            config.display_config()

            # Подключаем БД
            await db.connect()

            # Инициализируем handlers
            self.discord_handler = DiscordHandler(None)

            # VK handler (если включён)
            if config.VK_ENABLED:
                self.vk_handler = VKHandler(None)
                self.telegram_handler = TelegramHandler(self.discord_handler, self.vk_handler)
                self.vk_handler.telegram_sender = self.telegram_handler
            else:
                self.telegram_handler = TelegramHandler(self.discord_handler)

            # Связываем Discord
            self.discord_handler.telegram_sender = self.telegram_handler

            # Инициализируем
            await self.telegram_handler.initialize()
            await self.discord_handler.initialize()

            if config.VK_ENABLED and self.vk_handler:
                await self.vk_handler.initialize()

            logger.info("✅ Все компоненты инициализированы")

            if config.VK_ENABLED and self.vk_handler:
                self.delete_sync = DeleteSyncManager(self.telegram_handler, self.vk_handler)

        except Exception as e:
            logger.error(f"❌ Ошибка инициализации: {e}")
            raise

    async def start(self):
        """Запуск бота"""
        tasks = []

        try:
            self.is_running = True

            logger.info("✅ Бот запущен и работает")
            logger.info("Для остановки: Ctrl+C или sudo systemctl stop tg-discord-bridge")

            # Запускаем ботов параллельно
            telegram_task = asyncio.create_task(self.telegram_handler.start())
            discord_task = asyncio.create_task(self.discord_handler.start())
            tasks = [telegram_task, discord_task]

            # Добавляем VK если включён
            if config.VK_ENABLED and self.vk_handler:
                vk_task = asyncio.create_task(self.vk_handler.start())
                tasks.append(vk_task)

            #if self.delete_sync:
            #    delete_sync_task = asyncio.create_task(self.delete_sync.start())
            #    tasks.append(delete_sync_task)

            # Ждём сигнала остановки
            # Пингуем watchdog пока бот работает
            while not self.shutdown_event.is_set():

                # Проверяем что задачи живы
                failed = False
                for t in tasks:
                    if t.done():
                        exc = t.exception() if not t.cancelled() else None
                        if exc or t.cancelled():
                            logger.error(f"❌ Задача умерла: {t.get_name()}: {exc or 'cancelled'}")
                            failed = True
                if failed:
                    break

                # Пингуем systemd watchdog
                try:
                    import sdnotify
                    sdnotify.SystemdNotifier().notify("WATCHDOG=1")
                except ImportError:
                    # Fallback без sdnotify
                    if os.environ.get("WATCHDOG_USEC"):
                        try:
                            import socket
                            addr = os.environ.get("NOTIFY_SOCKET")
                            if addr:
                                sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                                sock.connect(addr)
                                sock.sendall(b"WATCHDOG=1")
                                sock.close()
                        except Exception:
                            pass

                await asyncio.sleep(30)

            logger.info("🛑 Получен сигнал остановки, завершаю работу...")

            # Останавливаем handlers
            await self.stop()

            # Принудительно отменяем задачи
            for task in tasks:
                task.cancel()

            # Даём время на завершение (максимум 5 секунд)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("⏰ Таймаут при остановке задач")
            except Exception as e:
                logger.error(f"❌ Ошибка при остановке задач: {e}")

        except Exception as e:
            logger.error(f"❌ Ошибка запуска: {e}")
            # Отменяем задачи при ошибке
            for task in tasks:
                if not task.done():
                    task.cancel()
            raise

    async def stop(self):
        """Остановка бота"""
        if not self.is_running:
            return

        logger.info("🛑 Остановка бота...")
        self.is_running = False

        try:
            # Останавливаем handlers
            if self.telegram_handler:
                try:
                    await asyncio.wait_for(self.telegram_handler.stop(), timeout=3.0)
                except asyncio.TimeoutError:
                    logger.warning("⏰ Таймаут при остановке Telegram handler")

            if self.discord_handler:
                try:
                    await asyncio.wait_for(self.discord_handler.stop(), timeout=3.0)
                except asyncio.TimeoutError:
                    logger.warning("⏰ Таймаут при остановке Discord handler")

                # ДОБАВЬТЕ:
            if config.VK_ENABLED and self.vk_handler:
                try:
                    await asyncio.wait_for(self.vk_handler.stop(), timeout=3.0)
                except asyncio.TimeoutError:
                    logger.warning("⏰ Таймаут при остановке VK handler")

            # Показываем статистику
            await self._show_statistics()

            if self.delete_sync:
                await self.delete_sync.stop()

            # Закрываем БД
            await db.close()

            logger.info("✅ Бот остановлен")

        except Exception as e:
            logger.error(f"❌ Ошибка при остановке: {e}")

    async def _show_statistics(self):
        """Показать статистику работы"""
        try:
            stats = await db.get_statistics()

            if stats:
                logger.info("=" * 50)
                logger.info("📊 СТАТИСТИКА")
                logger.info("=" * 50)
                logger.info(f"Всего сообщений: {stats.get('total_messages', 0)}")

                for source, dest, count in stats.get('routes', []):
                    logger.info(f"{source} → {dest}: {count}")

                logger.info(f"Ошибок: {stats.get('total_errors', 0)}")
                logger.info("=" * 50)

        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики: {e}")

    def request_shutdown(self):
        """Запросить остановку"""
        logger.info("📢 Запрос на остановку получен")
        self.shutdown_event.set()

# Глобальный экземпляр
bridge = BridgeBot()

def handle_signal(signum):
    """Обработчик сигналов"""
    logger.info(f"⚠️ Получен сигнал {signum}")
    bridge.request_shutdown()

async def main():
    """Главная функция"""

    # Регистрируем обработчики сигналов
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    try:
        # Инициализируем и запускаем
        await bridge.initialize()
        await bridge.start()

        logger.info("👋 Завершение работы")

    except KeyboardInterrupt:
        logger.info("⚠️ Прервано пользователем")
        await bridge.stop()

    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        await bridge.stop()
        sys.exit(1)

if __name__ == "__main__":
    try:
        # Устанавливаем политику событий для Windows (если нужно)
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("👋 До свидания!")
    except Exception as e:
        logger.error(f"❌ Необработанная ошибка: {e}", exc_info=True)
        sys.exit(1)