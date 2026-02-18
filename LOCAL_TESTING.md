# Локальное тестирование бота

Для тестирования бота локально используется **polling** режим (бот сам запрашивает обновления у Telegram).

## Шаги для запуска

### 1. Убедитесь, что webhook удален

Если ранее был установлен webhook на сервере, его нужно удалить перед локальным запуском:

```bash
python3 -m transcript_python_bot.delete_webhook
```

Или через pip:

```bash
transcript-delete-webhook
```

### 2. Настройте переменные окружения

Создайте файл `.env` в корне проекта с необходимыми переменными:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
OPENAI_API_KEY=your_openai_key_here

# Опционально:
TRANSCRIPT_LANGUAGES=ru,en
WEBSHARE_PROXY_USERNAME=your_proxy_username
WEBSHARE_PROXY_PASSWORD=your_proxy_password
WEBSHARE_PROXY_LOCATIONS=de,nl,pl
NOTION_API_KEY=your_notion_key
NOTION_DATABASE_ID=your_database_id
TELEGRAM_CHANNEL_ID=your_channel_id
```

### 3. Запустите бота

Есть несколько способов запуска:

**Вариант 1: Через main.py**
```bash
python main.py
```

**Вариант 2: Через модуль**
```bash
python3 -m transcript_python_bot
```

**Вариант 3: Через pip (если установлен)**
```bash
transcript-bot-polling
```

### 4. Проверьте работу

Бот должен начать получать сообщения. В логах вы увидите:
- `queue worker` - воркер очереди запущен
- `telegram text received` - получено сообщение
- `enqueue job` - задание добавлено в очередь
- `queue start` - начата обработка задания

## Остановка бота

Нажмите `Ctrl+C` для остановки. Бот корректно завершит работу и остановит воркер очереди.

## Переключение обратно на webhook

После локального тестирования, если нужно вернуться к webhook:

1. Остановите локальный бот (Ctrl+C)
2. Установите webhook на сервере:
   ```bash
   python3 -m transcript_python_bot.set_webhook
   ```

## Отладка

Для более подробных логов установите в `.env`:

```bash
LOG_LEVEL=DEBUG
HTTPX_LOG_LEVEL=INFO
TELEGRAM_LOG_LEVEL=INFO
```






