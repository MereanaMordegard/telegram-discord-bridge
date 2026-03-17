# Telegram-Discord Bridge Bot

Профессиональный бот для синхронизации сообщений между Telegram и Discord с поддержкой тредов.

## Возможности

✅ **Двусторонняя синхронизация** — Telegram ↔ Discord  
✅ **Поддержка Telegram тредов** — работа с конкретным тредом в супергруппе  
✅ **Медиафайлы** — изображения, видео, аудио, документы  
✅ **Автосжатие** — оптимизация размера изображений  
✅ **Rate limiting** — защита от флуда  
✅ **Очереди сообщений** — предотвращение потери сообщений  
✅ **База данных** — история всех сообщений  
✅ **Логирование** — подробные логи с ротацией  
✅ **Обработка ошибок** — graceful degradation  
✅ **Webhook поддержка** — улучшенное отображение в Discord  

## Установка

### 1. Клонирование репозитория
```bash
git clone <repo_url>
cd telegram-discord-bridge
```

### 2. Установка зависимостей
```bash
pip install -r requirements.txt
```

### 3. Настройка конфигурации

Скопируйте `.env.example` в `.env`:
```bash
cp .env.example .env
```

Отредактируйте `.env` и заполните необходимые токены:
```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_THREAD_ID=12345

DISCORD_BOT_TOKEN=MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GaBcDe.FgHiJkLmNoPqRsTuVwXyZ123456789
DISCORD_CHANNEL_ID=123456789012345678
```

### 4. Получение токенов и ID

#### Telegram Bot Token

1. Напишите [@BotFather](https://t.me/BotFather)
2. Отправьте `/newbot`
3. Следуйте инструкциям
4. Скопируйте токен
5. **Важно:** Добавьте бота в ваш чат как администратора

#### Telegram Chat ID

1. Добавьте [@RawDataBot](https://t.me/RawDataBot) в ваш чат
2. Он отправит информацию о чате
3. Скопируйте `"id"` (например: `-1001234567890`)
4. Удалите @RawDataBot из чата

#### Telegram Thread ID (опционально)

Если используете треды:
```python
# Временный скрипт get_thread_id.py
from telegram import Bot
import asyncio

async def get_thread_id():
    bot = Bot(token="ВАШ_ТОКЕН")
    updates = await bot.get_updates()
    
    for update in updates:
        if update.message and update.message.message_thread_id:
            print(f"Chat ID: {update.message.chat_id}")
            print(f"Thread ID: {update.message.message_thread_id}")
            print(f"Text: {update.message.text}")
            print("---")

asyncio.run(get_thread_id())
```

Отправьте сообщение в нужный тред и запустите скрипт:
```bash
python get_thread_id.py
```

#### Discord Bot Token

1. Перейдите на [Discord Developer Portal](https://discord.com/developers/applications)
2. Нажмите **New Application**
3. **Bot** → **Add Bot** → **Reset Token**
4. Скопируйте токен
5. Включите **Privileged Gateway Intents**:
   - ✅ MESSAGE CONTENT INTENT
   - ✅ SERVER MEMBERS INTENT
6. **OAuth2** → **URL Generator**:
   - Scopes: `bot`
   - Permissions: `Send Messages`, `Read Message History`, `Attach Files`
7. Скопируйте ссылку и пригласите бота на сервер

#### Discord Channel ID

1. В Discord: **User Settings** → **Advanced** → включите **Developer Mode**
2. ПКМ на нужном канале → **Copy ID**

#### Discord Webhook (опционально, для лучшего отображения)

1. ПКМ на канале → **Edit Channel**
2. **Integrations** → **Webhooks** → **New Webhook**
3. Скопируйте **Webhook URL**
4. Добавьте в `.env` как `DISCORD_WEBHOOK_URL`

## Запуск

### Обычный запуск
```bash
python main.py
```

### Запуск в фоне (Linux)
```bash
nohup python main.py > /dev/null 2>&1 &
```

### Systemd service (рекомендуется для production)

Создайте файл `/etc/systemd/system/tg-discord-bridge.service`:
```ini
[Unit]
Description=Telegram-Discord Bridge Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/telegram-discord-bridge
Environment="PATH=/path/to/venv/bin"
ExecStart=/path/to/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Запуск:
```bash
sudo systemctl daemon-reload
sudo systemctl enable tg-discord-bridge
sudo systemctl start tg-discord-bridge
sudo systemctl status tg-discord-bridge
```

Логи:
```bash
sudo journalctl -u tg-discord-bridge -f
```

### Docker (опционально)

Создайте `Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

Создайте `docker-compose.yml`:
```yaml
version: '3.8'

services:
  bridge:
    build: .
    container_name: tg-discord-bridge
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
      - ./bridge_history.db:/app/bridge_history.db
```

Запуск:
```bash
docker-compose up -d
docker-compose logs -f
```

## Использование

После запуска бот автоматически:

1. ✅ Пересылает сообщения из Telegram в Discord
2. ✅ Пересылает сообщения из Discord в Telegram
3. ✅ Обрабатывает медиафайлы (фото, видео, аудио, документы)
4. ✅ Сжимает большие изображения
5. ✅ Логирует все действия
6. ✅ Сохраняет историю в БД

## Мониторинг

### Логи

Логи сохраняются в файл `bridge.log` с автоматической ротацией:
```bash
tail -f bridge.log
```

### База данных

История сообщений в SQLite `bridge_history.db`:
```bash
sqlite3 bridge_history.db

# Примеры запросов:
SELECT COUNT(*) FROM messages;
SELECT * FROM messages ORDER BY timestamp DESC LIMIT 10;
SELECT * FROM errors ORDER BY timestamp DESC LIMIT 5;
```

### Статистика

При остановке бота (Ctrl+C) автоматически выводится статистика:
```
📊 СТАТИСТИКА
==================================================
Всего сообщений: 1234
telegram → discord: 678
discord → telegram: 556
Ошибок: 3
==================================================
```

## Безопасность

✅ **Валидация конфигурации** — проверка всех параметров при запуске  
✅ **Ограничение размера файлов** — защита от перегрузки  
✅ **Rate limiting** — защита от флуда  
✅ **Фильтрация типов файлов** — только разрешённые форматы  
✅ **Обработка ошибок** — graceful degradation  
✅ **Логирование ошибок** — все ошибки в БД  
✅ **Токены в .env** — не хранятся в коде  

## Настройка

Все параметры настраиваются в `.env`:
```env
# Ограничения
MAX_MESSAGE_LENGTH=4000        # Макс длина сообщения
MAX_FILE_SIZE_MB=10           # Макс размер файла в MB
ALLOWED_FILE_TYPES=image,video,audio,document

# Rate Limiting
RATE_LIMIT_MESSAGES=30        # Макс сообщений
RATE_LIMIT_PERIOD=60          # За период (секунды)

# Логирование
LOG_LEVEL=INFO                # DEBUG, INFO, WARNING, ERROR
LOG_FILE=./bridge.log
```

## Troubleshooting

### Бот не отвечает

1. Проверьте что токены корректны
2. Убедитесь что бот добавлен в чаты/каналы
3. Проверьте права бота (администратор в Telegram)
4. Посмотрите логи: `tail -f bridge.log`

### Медиафайлы не пересылаются

1. Проверьте `ALLOWED_FILE_TYPES` в `.env`
2. Проверьте размер файла (`MAX_FILE_SIZE_MB`)
3. Убедитесь что у бота есть права на отправку файлов

### Rate limit ошибки

1. Увеличьте `RATE_LIMIT_PERIOD` в `.env`
2. Уменьшите `RATE_LIMIT_MESSAGES`
3. Проверьте нет ли циклов пересылки

### Telegram "Bot was blocked by the user"

Пользователь заблокировал бота — удалите его ID из обработки

## Лицензия

MIT License

## Автор

Создано для ЖК Квартал Румянцево 🏠