"""
Обработка медиафайлов
"""
import asyncio
import aiohttp
import io
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image
from logger import get_logger
from config import config

logger = get_logger(__name__)


class MediaHandler:
    """Класс для работы с медиафайлами"""

    @staticmethod
    async def download_file(url: str, max_size_mb: int = None) -> Optional[bytes]:
        """
        Скачать файл по URL

        Args:
            url: URL файла
            max_size_mb: Максимальный размер в MB

        Returns:
            Байты файла или None при ошибке
        """
        max_size = (max_size_mb or config.MAX_FILE_SIZE_MB) * 1024 * 1024

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.error(f"❌ Ошибка скачивания: HTTP {response.status}")
                        return None

                    # Проверка размера
                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) > max_size:
                        logger.warning(
                            f"⚠️ Файл слишком большой: "
                            f"{int(content_length) / 1024 / 1024:.2f}MB"
                        )
                        return None

                    # Скачивание с ограничением размера
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        data.extend(chunk)
                        if len(data) > max_size:
                            logger.warning("⚠️ Превышен лимит размера при скачивании")
                            return None

                    logger.info(f"✅ Файл скачан: {len(data) / 1024:.2f}KB")
                    return bytes(data)

        except asyncio.TimeoutError:
            logger.error("❌ Таймаут при скачивании файла")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка скачивания файла: {e}")
            return None

    @staticmethod
    def compress_image(
            image_data: bytes,
            max_size_mb: float = 8.0,
            quality: int = 85
    ) -> Optional[bytes]:
        """
        Сжать изображение если оно слишком большое

        Args:
            image_data: Байты изображения
            max_size_mb: Максимальный размер в MB
            quality: Качество JPEG (1-100)

        Returns:
            Сжатые байты или оригинал
        """
        try:
            current_size_mb = len(image_data) / 1024 / 1024

            if current_size_mb <= max_size_mb:
                return image_data

            logger.info(f"🔄 Сжатие изображения ({current_size_mb:.2f}MB)")

            # Открываем изображение
            image = Image.open(io.BytesIO(image_data))

            # Конвертируем в RGB если нужно
            if image.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                background.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
                image = background

            # Уменьшаем размер если нужно
            max_dimension = 2000
            if max(image.size) > max_dimension:
                ratio = max_dimension / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            # Сохраняем с уменьшенным качеством
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=quality, optimize=True)
            compressed_data = output.getvalue()

            compressed_size_mb = len(compressed_data) / 1024 / 1024
            logger.info(
                f"✅ Изображение сжато: "
                f"{current_size_mb:.2f}MB → {compressed_size_mb:.2f}MB"
            )

            return compressed_data

        except Exception as e:
            logger.error(f"❌ Ошибка сжатия изображения: {e}")
            return image_data

    @staticmethod
    def get_file_type(filename: str) -> str:
        """Определить тип файла по расширению"""
        ext = Path(filename).suffix.lower()

        image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        video_exts = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
        audio_exts = {'.mp3', '.wav', '.ogg', '.m4a', '.flac'}

        if ext in image_exts:
            return 'image'
        elif ext in video_exts:
            return 'video'
        elif ext in audio_exts:
            return 'audio'
        else:
            return 'document'

    @staticmethod
    def is_allowed_file_type(file_type: str) -> bool:
        """Проверить разрешён ли тип файла"""
        return file_type in config.ALLOWED_FILE_TYPES


# Глобальный экземпляр
media_handler = MediaHandler()