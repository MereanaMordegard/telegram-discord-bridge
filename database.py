"""
База данных для хранения истории сообщений
"""
import aiosqlite
from datetime import datetime
from typing import Optional, Dict, Any
from logger import get_logger
from config import config

logger = get_logger(__name__)


class Database:
    """Класс для работы с базой данных"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.connection: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Подключение к базе данных"""
        try:
            self.connection = await aiosqlite.connect(self.db_path)
            await self._create_tables()
            logger.info(f"✅ Подключение к базе данных: {self.db_path}")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к БД: {e}")
            raise

    async def _create_tables(self):
        """Создание таблиц"""
        try:
            await self.connection.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    destination_id TEXT,
                    username TEXT,
                    content TEXT,
                    media_type TEXT,
                    thread_id INTEGER,
                    vk_peer_id INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'sent'
                )
            """)

            # Миграция: добавить колонки если БД уже существует без них
            await self._migrate_columns()

            await self.connection.execute("""
                CREATE TABLE IF NOT EXISTS errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_type TEXT,
                    error_message TEXT,
                    traceback TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await self.connection.commit()
            logger.info("✅ Таблицы БД созданы/проверены")
        except Exception as e:
            logger.error(f"❌ Ошибка создания таблиц: {e}")
            raise

    async def _migrate_columns(self):
        """Добавить новые колонки в существующую таблицу (если их нет)"""
        try:
            cursor = await self.connection.execute("PRAGMA table_info(messages)")
            existing_columns = {row[1] for row in await cursor.fetchall()}

            if 'thread_id' not in existing_columns:
                await self.connection.execute(
                    "ALTER TABLE messages ADD COLUMN thread_id INTEGER"
                )
                logger.info("📦 Миграция: добавлена колонка thread_id")

            if 'vk_peer_id' not in existing_columns:
                await self.connection.execute(
                    "ALTER TABLE messages ADD COLUMN vk_peer_id INTEGER"
                )
                logger.info("📦 Миграция: добавлена колонка vk_peer_id")
        except Exception as e:
            logger.error(f"❌ Ошибка миграции: {e}")

    async def log_message(
            self,
            source: str,
            destination: str,
            source_id: str,
            username: str,
            content: str = "",
            media_type: Optional[str] = None,
            destination_id: Optional[str] = None,
            thread_id: Optional[int] = None,
            vk_peer_id: Optional[int] = None
    ) -> int:
        """Логирование отправленного сообщения"""
        try:
            cursor = await self.connection.execute("""
                INSERT INTO messages 
                (source, destination, source_id, destination_id, username, content, media_type, thread_id, vk_peer_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (source, destination, source_id, destination_id, username, content, media_type, thread_id, vk_peer_id))

            await self.connection.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"❌ Ошибка логирования сообщения: {e}")
            return -1

    async def log_error(
            self,
            error_type: str,
            error_message: str,
            traceback: Optional[str] = None
    ):
        """Логирование ошибки"""
        try:
            await self.connection.execute("""
                INSERT INTO errors (error_type, error_message, traceback)
                VALUES (?, ?, ?)
            """, (error_type, error_message, traceback))

            await self.connection.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка логирования ошибки: {e}")

    async def get_statistics(self) -> Dict[str, Any]:
        """Получить статистику"""
        try:
            # Общее количество сообщений
            cursor = await self.connection.execute(
                "SELECT COUNT(*) FROM messages"
            )
            total_messages = (await cursor.fetchone())[0]

            # Сообщения по направлениям
            cursor = await self.connection.execute("""
                SELECT source, destination, COUNT(*) 
                FROM messages 
                GROUP BY source, destination
            """)
            routes = await cursor.fetchall()

            # Количество ошибок
            cursor = await self.connection.execute(
                "SELECT COUNT(*) FROM errors"
            )
            total_errors = (await cursor.fetchone())[0]

            return {
                'total_messages': total_messages,
                'routes': routes,
                'total_errors': total_errors
            }
        except Exception as e:
            logger.error(f"❌ Ошибка получения статистики: {e}")
            return {}

    async def close(self):
        """Закрытие соединения"""
        if self.connection:
            await self.connection.close()
            logger.info("🔌 База данных отключена")

    async def get_synced_messages(self, source: str, destination: str,
                                  since: datetime,
                                  until: datetime = None) -> list:
        """Получить синхронизированные сообщения за период"""
        try:
            since_str = since.strftime('%Y-%m-%d %H:%M:%S')
            params = [source, destination, since_str]

            query = """
                SELECT source_id, destination_id, thread_id, vk_peer_id
                FROM messages
                WHERE source = ? AND destination = ? AND timestamp >= ?
                    AND destination_id IS NOT NULL
            """

            if until:
                query += " AND timestamp <= ?"
                params.append(until.strftime('%Y-%m-%d %H:%M:%S'))

            query += " ORDER BY timestamp DESC"

            cursor = await self.connection.execute(query, params)
            rows = await cursor.fetchall()
            return [
                {
                    'source_id': row[0],
                    'destination_id': row[1],
                    'thread_id': row[2],
                    'vk_peer_id': row[3]
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"❌ DB get_synced_messages error: {e}")
            return []

    async def delete_synced_message(self, source: str, source_id: str):
        """Удалить запись о синхронизированном сообщении"""
        try:
            await self.connection.execute(
                "DELETE FROM messages WHERE source = ? AND source_id = ?",
                (source, source_id)
            )
            await self.connection.commit()
        except Exception as e:
            logger.error(f"❌ DB delete_synced_message error: {e}")

# Глобальный экземпляр БД
db = Database(config.DATABASE_PATH)