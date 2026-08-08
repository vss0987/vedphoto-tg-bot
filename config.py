import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
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

DB_PATH = os.getenv("DB_PATH", "bot_database.db")
