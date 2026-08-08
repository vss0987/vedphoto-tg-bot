"""
Полный лог взаимодействий пользователя с ботом.

Реализовано через aiogram outer-middleware на уровне Update, а не точечными
вызовами db.log_interaction() из отдельных хендлеров — так гарантированно
логируется КАЖДОЕ взаимодействие (команды, нажатия кнопок меню, инлайн-кнопки
календаря, отправка контакта и т.д.), включая те места, где отдельный вызов
логирования легко забыть добавить.

Хранение — обычная таблица SQLite (interaction_log в database.py), это и есть
"best practice" формат: нормализованные строки, которые тривиально выгружаются
в CSV (см. export_interactions_csv) и не требуют парсинга при архивации.

Раз в неделю (по умолчанию — каждый понедельник в 00:00) фоновая задача
(см. weekly_archive_task в main.py) выгружает всё накопленное в CSV-файл,
отправляет его админу в Telegram и очищает таблицу.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from aiogram import BaseMiddleware, Bot
from aiogram.types import Update, TelegramObject, FSInputFile

import config
from database import db

logger = logging.getLogger(__name__)

# Каталог для CSV-архивов на диске (помимо отправки админу в Telegram —
# так файлы не теряются, если сообщение боту в Telegram недоступно/удалено).
ARCHIVE_DIR = Path(os.getenv("INTERACTION_ARCHIVE_DIR", "archives"))


class InteractionLoggerMiddleware(BaseMiddleware):
    """Логирует каждый входящий Update: кто, что за взаимодействие, когда,
    в каком разделе бота. По ТЗ — только для пользователей, которые уже
    хотя бы раз выполнили /start (либо это и есть тот самый /start)."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if isinstance(event, Update):
            try:
                await self._log(event)
            except Exception:
                # Логирование не должно мешать основной работе бота
                logger.exception("Interaction logging failed")
        return await handler(event, data)

    @staticmethod
    async def _log(event: Update) -> None:
        user = None
        interaction_type = None
        section = None
        details = None

        if event.message:
            msg = event.message
            user = msg.from_user

            if msg.contact:
                interaction_type = "contact"
                section = "phone_share"
                details = "номер телефона отправлен"
            elif msg.text and msg.text.startswith("/"):
                interaction_type = "command"
                section = msg.text.split()[0]
                details = msg.text
            elif msg.text:
                interaction_type = "message"
                # Для reply-кнопок текст сообщения и есть название раздела
                # ("📅 Календарь", "💰 Стоимость" и т.п.) — само по себе
                # информативно, отдельный маппинг не нужен.
                section = msg.text
                details = msg.text
            else:
                interaction_type = "message"
                section = msg.content_type
                details = msg.content_type

        elif event.callback_query:
            cq = event.callback_query
            user = cq.from_user
            interaction_type = "callback"
            section = cq.data
            details = cq.data

        if user is None:
            return

        # "Всё взаимодействие после запуска /start": логируем либо уже
        # зарегистрированных пользователей, либо сам /start (чтобы не терять
        # самое первое событие — регистрация происходит в хендлере ПОСЛЕ
        # прохождения этого middleware).
        is_start_command = interaction_type == "command" and section == "/start"
        if not is_start_command:
            known_user = await db.get_user(user.id)
            if not known_user:
                return

        await db.log_interaction(
            user_id=user.id,
            full_name=user.full_name,
            username=user.username,
            interaction_type=interaction_type,
            section=section,
            details=details,
        )


async def archive_interaction_log(bot: Bot | None = None) -> tuple[int, str] | None:
    """
    Выгружает весь текущий interaction_log в CSV, отправляет файл админу
    (если бот и config.ADMIN_CHAT_ID доступны) и очищает таблицу.

    Возвращает (количество_записей, путь_к_файлу) либо None, если лог был пуст.
    """
    count_before = await db.get_interaction_log_count()
    if count_before == 0:
        logger.info("Interaction log is empty — nothing to archive")
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"interactions_{datetime.now().strftime('%Y-%m-%d')}.csv"
    out_path = str(ARCHIVE_DIR / filename)

    exported = await db.export_interactions_csv(out_path)
    if exported == 0:
        logger.warning("Interaction log export produced 0 rows despite count=%s", count_before)
        return None

    deleted = await db.clear_interaction_log()
    logger.info("Archived %s interactions to %s and cleared the log", deleted, out_path)

    if bot and config.ADMIN_CHAT_ID:
        try:
            await bot.send_document(
                config.ADMIN_CHAT_ID,
                FSInputFile(out_path),
                caption=f"📦 Еженедельный архив взаимодействий: {deleted} записей",
            )
        except Exception:
            logger.exception("Failed to send weekly interaction archive to admin")

    return deleted, out_path
