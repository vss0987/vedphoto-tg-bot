"""Общие утилиты для уведомлений админа (config.ADMIN_CHAT_ID)."""

import html
import logging

from aiogram import Bot

import config

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Единый источник правды о том, кто админ — сверяемся с config.ADMIN_CHAT_ID."""
    return config.ADMIN_CHAT_ID is not None and user_id == config.ADMIN_CHAT_ID


def user_link(user_id: int, username: str | None, display_name: str | None = None) -> str:
    """
    Кликабельная HTML-ссылка на пользователя для сообщений админу.

    Если у пользователя есть username — обычная ссылка t.me/username
    (открывается в любом клиенте). Если username нет — используется
    tg://user?id=<id>: такая ссылка тоже открывает чат с пользователем
    по его числовому ID и поддерживается Telegram-клиентами, даже если
    у пользователя нет публичного username.
    """
    label = html.escape(display_name or (f"@{username}" if username else str(user_id)))
    if username:
        return f'<a href="https://t.me/{username}">{label}</a>'
    return f'<a href="tg://user?id={user_id}">{label}</a>'


async def notify_admin(bot: Bot | None, text: str) -> None:
    """Отправляет сообщение в чат админа, если он настроен (config.ADMIN_CHAT_ID)."""
    if not bot or not config.ADMIN_CHAT_ID:
        return
    try:
        await bot.send_message(config.ADMIN_CHAT_ID, text, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to notify admin")
