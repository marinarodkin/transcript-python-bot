

curl -X POST "https://api.telegram.org/bot6097284371:AAHfh2CcAiGMeOLJRmnhUhD5xYD-XfiASYw/setWebhook" \
  -d "url=https://marinar.eu.pythonanywhere.com/telegram/2d7b4b9f6c1a4e9c9c2f6c6a1b8b7f0e" \
  -d "secret_token=223339857flksdhfdkfljdsf"

### Как деплоить изменения

Локально (в этом репозитории):
1. `git status -sb`
2. `git add <files>`
3. `git commit -m "описание"`
4. `git push`

На PythonAnywhere (Bash console):
1. `cd ~/path/to/transcript-python-bot`
2. `git pull`
3. При изменении зависимостей: `pip install -r requirements.txt`

Перезапуск:
1. PythonAnywhere Web tab → кнопка `Reload` (перезапускает WSGI приложение).
