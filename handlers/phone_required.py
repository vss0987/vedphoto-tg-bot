"""
Запрос номера телефона перед подтверждением записи.

Этот модуль отвечал за экспорт `phone_router` и функции
`request_phone_number`, но в загруженном проекте файл содержал
случайно продублированное содержимое `calendar_menu_handler.py`
(без `phone_router` и без `request_phone_number`). Из-за этого:
  - handlers/__init__.py падал с ImportError на `from .phone_required import phone_router`
  - calendar_menu_handler.py падал с ImportError на
    `from handlers.phone_required import request_phone_number`
  - бот не мог запуститься вообще.

Ниже — рабочая реализация.
"""

import logging

from aiogram import Router, F, Bot
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.fsm.context import FSMContext

from constants import TYPE_NAMES
from database import db, SlotTakenError
from keyboards.menu_keyboard import get_menu_keyboard
from handlers.admin_notify import notify_admin, user_link

phone_router = Router()
logger = logging.getLogger(__name__)


def get_phone_request_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой 'Поделиться номером' + отмена."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📞 Поделиться номером", request_contact=True))
    builder.row(KeyboardButton(text="⬅️ Отмена"))
    return builder.as_markup(resize_keyboard=True)


async def _notify_admin_new_booking(bot: Bot | None, booking: dict) -> None:
    type_name = TYPE_NAMES.get(booking["photosession_type"], booking["photosession_type"])

    user = await db.get_user(booking["user_id"])
    username = user["username"] if user else None
    phone = user["phone"] if user else None
    link = user_link(booking["user_id"], username, booking.get("full_name"))

    phone_line = f"📞 {phone}\n" if phone else ""

    await notify_admin(
        bot,
        "🆕 <b>Новая запись</b>\n\n"
        f"👤 {link}\n"
        f"{phone_line}"
        f"📸 {type_name}\n"
        f"📅 {booking['booking_date']}\n"
        f"⏰ {booking['booking_time']}\n"
        f"#{booking['order_id']}",
    )


async def finalize_booking(
    answer,
    user_id: int,
    context_data: dict,
    success_message: str,
    bot: Bot | None = None,
) -> None:
    """
    Создаёт бронь в БД по сохранённым данным и отвечает пользователю.

    answer — вызываемый объект вида `message.answer` (или похожий),
    принимающий (text, parse_mode=..., reply_markup=...).
    """
    try:
        booking = await db.create_booking(
            user_id=user_id,
            photosession_type=context_data["photosession_type"],
            booking_date=context_data["booking_date"],
            booking_time=context_data["booking_time"],
            notes=context_data.get("notes", ""),
        )
    except SlotTakenError:
        await answer(
            "❌ <b>Извините, этот слот уже успели забронировать.</b>\n"
            "Пожалуйста, выберите другое время в календаре.",
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(),
        )
        return

    if booking:
        await answer(success_message, parse_mode="HTML", reply_markup=get_menu_keyboard())
        await _notify_admin_new_booking(bot, booking)
    else:
        await answer(
            "❌ Не удалось создать бронь. Попробуйте позже.",
            reply_markup=get_menu_keyboard(),
        )


async def request_phone_number(
    target,
    state: FSMContext,
    context_data: dict,
    success_message: str,
    is_callback: bool = False,
    bot: Bot | None = None,
) -> None:
    """
    Точка входа, вызываемая из calendar_menu_handler.py при выборе времени.

    Если номер телефона у пользователя уже сохранён — бронь создаётся сразу.
    Если нет — пользователю показывается кнопка "Поделиться номером",
    а данные брони (context_data) сохраняются в FSM-состоянии, чтобы
    contact.py мог завершить бронирование после получения контакта.

    target — Message (если is_callback=False) или CallbackQuery (если True).
    """
    if is_callback:
        callback: CallbackQuery = target
        message = callback.message
        user_id = callback.from_user.id
    else:
        message: Message = target
        user_id = message.from_user.id

    phone = await db.get_user_phone(user_id)

    if phone:
        await finalize_booking(message.answer, user_id, context_data, success_message, bot=bot)
        return

    # Сохраняем данные о брони в FSM, чтобы забрать их в contact.py
    await state.update_data(
        pending_booking=context_data,
        pending_success_message=success_message,
    )

    await message.answer(
        "📞 <b>Зачем нужен номер телефона</b>\n\n"
        "Он нужен, чтобы подтвердить вашу запись и, если потребуется, "
        "связаться с вами по поводу именно вашей фотосессии — например, "
        "уточнить детали или предупредить об изменениях.\n\n"
        "• Номер не передаётся третьим лицам и не используется в рекламных целях.\n"
        "• Используется только в рамках работы с вашими записями.\n"
        "• Хранится, пока вы пользуетесь ботом. Если захотите, чтобы его "
        "удалили — напишите об этом через «❓ Спросить», и мы удалим номер "
        "из базы.\n\n"
        "Нажмите кнопку ниже, чтобы поделиться контактом (это займёт пару "
        "секунд), либо отмените запись.",
        parse_mode="HTML",
        reply_markup=get_phone_request_keyboard(),
    )


@phone_router.message(F.text == "⬅️ Отмена")
async def cancel_phone_request(message: Message, state: FSMContext):
    """Отмена запроса номера телефона (и отложенной брони, если была)."""
    data = await state.get_data()
    if "pending_booking" not in data:
        # Не в процессе запроса телефона — не наш случай, пусть ловят другие хендлеры
        return

    await state.clear()
    await message.answer(
        "Запись отменена.",
        reply_markup=get_menu_keyboard(),
    )
