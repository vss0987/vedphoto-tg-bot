import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import config
from database import db
from handlers import (
    start_router,
    photosession_router,
    calendar_router,
    feedback_router,
    contact_router,
    phone_router,
    admin_router,
)
from handlers.interaction_logger import InteractionLoggerMiddleware, archive_interaction_log
from keyboards.menu_keyboard import get_menu_keyboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_menu_keyboard())


def _seconds_until_next_monday_midnight() -> float:
    now = datetime.now()
    days_until_monday = (7 - now.weekday()) % 7  # weekday(): Monday=0
    next_monday = (now + timedelta(days=days_until_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if next_monday <= now:
        next_monday += timedelta(days=7)
    return (next_monday - now).total_seconds()


async def weekly_interaction_archive_task(bot: Bot):
    """Раз в неделю (понедельник 00:00) выгружает лог взаимодействий в CSV,
    отправляет его админу и очищает таблицу interaction_log."""
    while True:
        sleep_seconds = _seconds_until_next_monday_midnight()
        logger.info(
            "Next interaction log archive in %.0f seconds (%.1f hours)",
            sleep_seconds, sleep_seconds / 3600,
        )
        await asyncio.sleep(sleep_seconds)
        try:
            result = await archive_interaction_log(bot)
            if result:
                count, path = result
                logger.info("Weekly interaction archive done: %s rows -> %s", count, path)
            else:
                logger.info("Weekly interaction archive: log was empty, nothing to do")
        except Exception:
            logger.exception("Weekly interaction log archive failed")


async def main():
    bot = Bot(
        token=config.TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Логируем КАЖДЫЙ апдейт (сообщения, нажатия кнопок, контакты и т.д.) —
    # outer-middleware на уровне Update гарантирует, что ни одно
    # взаимодействие не будет пропущено, даже если для конкретного хендлера
    # забыли добавить точечный вызов логирования.
    dp.update.outer_middleware(InteractionLoggerMiddleware())

    dp.message.register(cmd_cancel, Command("cancel"))

    await db.connect()

    dp.include_router(start_router)
    dp.include_router(photosession_router)
    dp.include_router(calendar_router)
    # contact_router и phone_router должны идти РАНЬШЕ feedback_router:
    # у feedback_router есть хендлер-"ловушка" без фильтров
    # (handle_other_messages), который иначе перехватывал бы ВСЕ
    # сообщения раньше, чем они доходили бы до обработки контакта/номера.
    dp.include_router(contact_router)
    dp.include_router(phone_router)
    dp.include_router(admin_router)
    dp.include_router(feedback_router)

    @dp.errors()
    async def on_error(event):
        logger.exception("Unhandled error while processing update: %s", event.exception)
        return True

    archive_task = asyncio.create_task(weekly_interaction_archive_task(bot))

    logger.info("Bot started")

    try:
        await dp.start_polling(bot)
    finally:
        archive_task.cancel()
        await db.close()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception:
        logger.exception("Fatal error")
