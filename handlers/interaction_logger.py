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

import csv
import io
import logging
import os
from datetime import datetime
from pathlib import Path

from aiogram import BaseMiddleware, Bot
from aiogram.types import Update, TelegramObject, FSInputFile

import config
from database import db
from crypto_utils import encrypt_bytes, is_encryption_configured

logger = logging.getLogger(__name__)

# Каталог для CSV-архивов на диске (помимо отправки админу в Telegram —
# так файлы не теряются, если сообщение боту в Telegram недоступно/удалено).
ARCHIVE_DIR = Path(os.getenv("INTERACTION_ARCHIVE_DIR", "archives"))


def _collect_known_button_texts() -> frozenset:
    """Собирает тексты всех reply-кнопок бота (динамически, из самих
    клавиатур) — чтобы отличать нажатие известной кнопки меню (это и есть
    'раздел бота', который безопасно логировать как есть) от произвольного
    текста, который человек напечатал сам (может содержать телефон, адрес
    и другие личные данные — такое в лог взаимодействий попадать не должно,
    у него другая задача: не хранить сообщения, а показывать разделы)."""
    try:
        from keyboards.menu_keyboard import get_menu_keyboard
        from keyboards.photosession_keyboard import get_photosession_keyboard
        from handlers.phone_required import get_phone_request_keyboard

        texts = set()
        for markup in (get_menu_keyboard(), get_photosession_keyboard(), get_phone_request_keyboard()):
            for row in markup.keyboard:
                for button in row:
                    texts.add(button.text)
        return frozenset(texts)
    except Exception:
        logger.exception("Failed to collect known button texts for interaction logger")
        return frozenset()


KNOWN_BUTTON_TEXTS = _collect_known_button_texts()


class InteractionLoggerMiddleware(BaseMiddleware):
    """Логирует каждый входящий Update: кто, что за взаимодействие, когда,
    в каком разделе бота. По ТЗ — только для пользователей, которые уже
    хотя бы раз выполнили /start (либо это и есть тот самый /start).

    Важно: сохраняется только НАЗВАНИЕ раздела (текст кнопки, callback_data),
    а не произвольный текст, который пользователь напечатал сам — см.
    KNOWN_BUTTON_TEXTS выше."""

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
                if msg.text in KNOWN_BUTTON_TEXTS:
                    # Известная кнопка меню — это и есть "раздел", безопасно
                    # сохранить как есть.
                    section = msg.text
                    details = msg.text
                else:
                    # Свободный текст от пользователя (например, вопрос
                    # админу) — может содержать личные данные. Фиксируем
                    # только сам факт свободного ввода, без содержимого.
                    section = "free_text_input"
                    details = None
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
    Выгружает весь текущий interaction_log в CSV **в памяти**, шифрует его
    (см. crypto_utils.py) и только зашифрованный результат пишет на диск —
    plaintext-версия на диск никогда не попадает. Отправляет зашифрованный
    файл админу (если бот и config.ADMIN_CHAT_ID доступны) и очищает таблицу.

    Если ключ шифрования не настроен, архивация НЕ выполняется (данные
    остаются в таблице как есть, ничего не теряется) — это осознанный выбор:
    лучше отсутствие архива за эту неделю, чем архив с личными данными
    пользователей в открытом виде.

    Возвращает (количество_записей, путь_к_файлу) либо None, если лог был
    пуст или архивация не удалась.
    """
    count_before = await db.get_interaction_log_count()
    if count_before == 0:
        logger.info("Interaction log is empty — nothing to archive")
        return None

    if not is_encryption_configured():
        logger.error(
            "public_key.pem не найден — архивация пропущена, "
            "данные (%s записей) остаются в interaction_log как есть.",
            count_before,
        )
        if bot and config.ADMIN_CHAT_ID:
            try:
                await bot.send_message(
                    config.ADMIN_CHAT_ID,
                    "⚠️ Не удалось заархивировать лог взаимодействий: на сервере "
                    "нет public_key.pem. Данные не потеряны, остались в базе — "
                    "сгенерируйте ключи локально (scripts/generate_keypair.py), "
                    "скопируйте public_key.pem на сервер и запустите архивацию "
                    "вручную кнопкой «📦 Архив взаимодействий» в админ-панели.",
                )
            except Exception:
                logger.exception("Failed to notify admin about missing encryption key")
        return None

    rows = await db.export_interactions_rows()
    if not rows:
        logger.warning("Interaction log count=%s but export returned 0 rows", count_before)
        return None

    header = [
        "log_id", "user_id", "full_name", "username",
        "interaction_type", "section", "details", "created_at",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    csv_bytes = buffer.getvalue().encode("utf-8-sig")  # BOM — чтобы Excel не ломал кириллицу

    try:
        encrypted = encrypt_bytes(csv_bytes)
    except Exception:
        logger.exception("Failed to encrypt interaction log export")
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"interactions_{datetime.now().strftime('%Y-%m-%d')}.csv.enc"
    out_path = str(ARCHIVE_DIR / filename)

    try:
        with open(out_path, "wb") as f:
            f.write(encrypted)
    except Exception:
        logger.exception("Failed to write encrypted archive to %s", out_path)
        return None

    deleted = await db.clear_interaction_log()
    logger.info("Archived %s interactions to %s (encrypted) and cleared the log", deleted, out_path)

    if bot and config.ADMIN_CHAT_ID:
        try:
            await bot.send_document(
                config.ADMIN_CHAT_ID,
                FSInputFile(out_path),
                caption=(
                    f"📦 Еженедельный архив взаимодействий: {deleted} записей\n"
                    f"🔒 Файл зашифрован — расшифровать: "
                    f"python scripts/decrypt_archive.py {filename}"
                ),
            )
        except Exception:
            logger.exception("Failed to send weekly interaction archive to admin")

    return deleted, out_path
