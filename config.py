import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не найден. Создайте файл .env (см. .env.example) "
        "и укажите в нём BOT_TOKEN=<ваш токен от @BotFather>."
    )

_admin_chat_id_raw = os.getenv("ADMIN_CHAT_ID")
try:
    ADMIN_CHAT_ID = int(_admin_chat_id_raw) if _admin_chat_id_raw else None
except ValueError:
    ADMIN_CHAT_ID = None

DB_PATH = os.getenv("DB_PATH", "/data/bot_database.db")

# Примечание: ключ шифрования архивов (BACKUP_ENCRYPTION_KEY) читается
# отдельно в crypto_utils.py, а не отсюда — намеренно, чтобы скрипты
# расшифровки архивов могли работать на машине без полного .env бота
# (config.py требует обязательный BOT_TOKEN, crypto_utils.py — нет).
