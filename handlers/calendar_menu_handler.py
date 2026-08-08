import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from datetime import date, timedelta

from database import db, OrderNotFoundError, SlotTakenError
from constants import TYPE_NAMES, STATUS_EMOJI
from services.booking_service import booking_service
from keyboards.calendar_keyboard import (
    get_main_calendar_keyboard,
    get_date_detail_keyboard,
    get_time_slots_keyboard,
    get_booking_list_keyboard,
    get_session_type_keyboard,
)
from keyboards.menu_keyboard import get_menu_keyboard
from handlers.phone_required import request_phone_number
from handlers.admin_notify import is_admin, user_link

calendar_router = Router()
logger = logging.getLogger(__name__)
MONTHS = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


async def _calendar_markup(user_id: int, year: int, month: int):
    if is_admin(user_id):
        booking_days = await db.get_all_booking_days_in_month(year, month)
    else:
        booking_days = await booking_service.get_user_booking_days(user_id, year, month)
    blocked_days = await db.get_blocked_days_in_month(year, month)
    return get_main_calendar_keyboard(year, month, booking_days, blocked_days)


@calendar_router.message(F.text == "📅 Календарь")
async def show_calendar(message: Message):
    user_id = message.from_user.id

    if not await db.get_user(user_id):
        await message.answer(
            "Пожалуйста, сначала зарегистрируйтесь через /start",
            reply_markup=get_menu_keyboard(),
        )
        return

    await db.clear_user_state(user_id)
    year, month = booking_service.get_current_month()

    await message.answer(
        f"📅 <b>{MONTHS[month]} {year}</b>\n\n"
        f"Выберите дату для записи",
        reply_markup=await _calendar_markup(user_id, year, month),
        parse_mode="HTML",
    )


@calendar_router.callback_query(F.data.startswith("month_"))
async def change_month(callback: CallbackQuery):
    _, year, month = callback.data.split("_")
    year, month = int(year), int(month)
    user_id = callback.from_user.id

    await callback.message.edit_text(
        f"📅 <b>{MONTHS[month]} {year}</b>\n\n"
        f"Выберите дату для записи",
        reply_markup=await _calendar_markup(user_id, year, month),
        parse_mode="HTML",
    )
    await callback.answer()


@calendar_router.callback_query(F.data.startswith("date_"))
async def show_date_detail(callback: CallbackQuery):
    target_date = booking_service.parse_date_callback(callback.data)
    user_id = callback.from_user.id
    admin = is_admin(user_id)

    nav_state = await db.get_user_state(user_id)

    if nav_state and nav_state.get("current_menu") == "reschedule":
        await _show_time_slots(
            callback,
            target_date,
            reschedule_order_id=int(nav_state["current_type"]),
        )
        return

    if admin and nav_state and nav_state.get("current_menu") == "block_range_start":
        await _finish_block_range(callback, nav_state, target_date)
        return

    date_str = target_date.strftime("%Y-%m-%d")
    is_blocked = await db.is_date_blocked(date_str)

    bookings = await booking_service.get_bookings_by_date(user_id, target_date)

    if bookings:
        text = (
            f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n"
            f"{'-' * 20}\n"
        )
        for booking in bookings:
            text += f"{booking_service.format_booking_text(booking)}\n{'-' * 20}\n"
        text += "Выберите действие:"
        booking = bookings[0]
    else:
        status_line = "🚫 День закрыт для записи" if is_blocked else "✅ Свободный день"
        text = (
            f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n"
            f"{'-' * 20}\n"
            f"{status_line}\n"
            f"{'-' * 20}\n"
        )
        text += "Хотите забронировать?" if not is_blocked else "Выберите действие:"
        booking = None

    await callback.message.edit_text(
        text,
        reply_markup=get_date_detail_keyboard(
            booking, target_date, is_admin=admin, is_blocked=is_blocked
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@calendar_router.callback_query(F.data.startswith("booking_"))
async def show_booking_detail(callback: CallbackQuery):
    _, order_id = callback.data.split("_")
    order_id = int(order_id)
    user_id = callback.from_user.id

    order = await db.get_order_for_user(order_id, user_id)
    if not order:
        await callback.answer("Бронь не найдена", show_alert=True)
        return

    booking = booking_service.order_to_booking(order)
    text = (
        f"📋 <b>Детали бронирования #{booking.order_id}</b>\n"
        f"{'-' * 20}\n"
        f"{booking_service.format_booking_text(booking)}\n"
        f"{'-' * 20}\n"
        f"Выберите действие:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_date_detail_keyboard(booking, booking.date or date.today()),
        parse_mode="HTML",
    )
    await callback.answer()


@calendar_router.callback_query(F.data.startswith("show_my_bookings"))
async def show_my_bookings(callback: CallbackQuery):
    user_id = callback.from_user.id
    bookings = await booking_service.get_user_bookings(user_id, active_only=True)

    if not bookings:
        await callback.message.edit_text(
            "📋 <b>Мои бронирования</b>\n\n"
            "У вас пока нет активных броней.\n"
            "Вернитесь в календарь и выберите свободный день!",
            reply_markup=get_booking_list_keyboard([]),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    text = "📋 <b>Мои бронирования</b>\n\n"
    for b in bookings:
        emoji = STATUS_EMOJI.get(b.status, "📌")
        date_str = b.date.strftime("%d.%m") if b.date else "—"
        text += (
            f"{emoji} {date_str} - {b.time or '—'} "
            f"({TYPE_NAMES.get(b.photosession_type, b.photosession_type)})\n"
        )
    text += "\nВыберите бронь для управления:"

    await callback.message.edit_text(
        text,
        reply_markup=get_booking_list_keyboard(bookings),
        parse_mode="HTML",
    )
    await callback.answer()


@calendar_router.callback_query(F.data.regexp(r"^cancel_\d+$"))
async def cancel_booking_prompt(callback: CallbackQuery):
    order_id = int(callback.data.split("_", 1)[1])
    user_id = callback.from_user.id

    if not await db.get_order_for_user(order_id, user_id):
        await callback.answer("Бронь не найдена", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, отменить", callback_data=f"confirm_cancel_{order_id}")
    builder.button(text="❌ Нет, вернуться", callback_data="back_to_calendar")
    builder.adjust(2)

    await callback.message.edit_text(
        "⚠️ <b>Точно отменить бронь?</b>\n\nЭто действие нельзя отменить.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@calendar_router.callback_query(F.data.startswith("confirm_cancel_"))
async def confirm_cancel(callback: CallbackQuery):
    order_id = int(callback.data.split("_", 2)[2])
    user_id = callback.from_user.id

    try:
        success = await booking_service.cancel_booking(order_id, user_id)
    except OrderNotFoundError:
        await callback.answer("Бронь не найдена", show_alert=True)
        return

    if success:
        await callback.message.edit_text(
            "✅ <b>Бронь успешно отменена</b>\n\n"
            "Если хотите, можете выбрать другую дату в календаре.",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            "❌ <b>Ошибка при отмене</b>\n\n"
            "Попробуйте позже или свяжитесь с поддержкой.",
            parse_mode="HTML",
        )

    await callback.message.answer("Главное меню:", reply_markup=get_menu_keyboard())
    await callback.answer()


@calendar_router.callback_query(F.data.startswith("reschedule_start_"))
async def reschedule_start(callback: CallbackQuery):
    order_id = int(callback.data.split("_", 2)[2])
    user_id = callback.from_user.id

    if not await db.get_order_for_user(order_id, user_id):
        await callback.answer("Бронь не найдена", show_alert=True)
        return

    await db.save_user_state(user_id, menu="reschedule", type=str(order_id))
    year, month = booking_service.get_current_month()

    await callback.message.edit_text(
        "📅 <b>Перенос брони</b>\n\n"
        "Выберите новую дату в календаре:",
        reply_markup=await _calendar_markup(user_id, year, month),
        parse_mode="HTML",
    )
    await callback.answer()


async def _show_time_slots(
        callback: CallbackQuery,
        target_date: date,
        photosession_type: str | None = None,
        reschedule_order_id: int | None = None,
):
    user_id = callback.from_user.id

    if await db.is_date_blocked(target_date.strftime("%Y-%m-%d")):
        await callback.message.edit_text(
            f"🚫 <b>{target_date.strftime('%d.%m.%Y')} закрыт для записи</b>\n\n"
            f"Пожалуйста, выберите другую дату.",
            reply_markup=await _calendar_markup(user_id, target_date.year, target_date.month),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    exclude_id = reschedule_order_id
    available_slots = await booking_service.get_available_slots(target_date, exclude_id)

    if not available_slots:
        await callback.message.edit_text(
            f"❌ <b>Нет свободных слотов</b>\n\n"
            f"На {target_date.strftime('%d.%m.%Y')} всё занято.\n"
            f"Выберите другую дату.",
            reply_markup=await _calendar_markup(user_id, target_date.year, target_date.month),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if photosession_type:
        type_label = TYPE_NAMES.get(photosession_type, photosession_type)
        extra = f"\n📸 Тип: {type_label}"
    else:
        extra = ""

    text = (
        f"📝 <b>{'Перенос брони на' if reschedule_order_id else 'Бронирование на'} "
        f"{target_date.strftime('%d.%m.%Y')}</b>{extra}\n\n"
        f"Выберите удобное время:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_time_slots_keyboard(target_date, available_slots),
        parse_mode="HTML",
    )
    await callback.answer()


@calendar_router.callback_query(F.data.startswith("book_"))
async def start_booking(callback: CallbackQuery):
    date_str = callback.data.split("_", 1)[1]
    target_date = booking_service.str_to_date(date_str)
    user_id = callback.from_user.id

    state = await db.get_user_state(user_id)
    photosession_type = state.get("current_type") if state else None

    if photosession_type and photosession_type in TYPE_NAMES:
        await _show_time_slots(callback, target_date, photosession_type=photosession_type)
        return

    await callback.message.edit_text(
        f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n\n"
        f"Выберите тип фотосессии:",
        reply_markup=get_session_type_keyboard(date_str),
        parse_mode="HTML",
    )
    await callback.answer()


@calendar_router.callback_query(F.data.startswith("picktype_"))
async def pick_session_type(callback: CallbackQuery):
    _, date_str, photosession_type = callback.data.split("_", 2)
    target_date = booking_service.str_to_date(date_str)
    user_id = callback.from_user.id

    await db.save_user_state(user_id, menu="booking", type=photosession_type)
    await _show_time_slots(callback, target_date, photosession_type=photosession_type)


@calendar_router.callback_query(F.data.startswith("time_"))
async def select_time(callback: CallbackQuery, state: FSMContext, bot: Bot):
    parts = callback.data.split("_")
    date_str = parts[1]
    time_str = parts[2]
    target_date = booking_service.str_to_date(date_str)
    time = f"{time_str[:2]}:{time_str[2:]}"
    user_id = callback.from_user.id
    nav_state = await db.get_user_state(user_id)

    if nav_state and nav_state.get("current_menu") == "reschedule":
        order_id = int(nav_state["current_type"])
        try:
            success = await booking_service.reschedule_booking(
                order_id, user_id, target_date, time
            )
        except OrderNotFoundError:
            await callback.answer("Бронь не найдена", show_alert=True)
            return
        except SlotTakenError:
            await callback.message.edit_text(
                "❌ <b>Этот слот уже занят.</b> Выберите другое время.",
                parse_mode="HTML",
            )
            await callback.answer()
            return

        await db.clear_user_state(user_id)

        if success:
            await callback.message.edit_text(
                f"✅ <b>Бронь перенесена!</b>\n\n"
                f"📅 {target_date.strftime('%d.%m.%Y')}\n"
                f"⏰ {time}\n\n"
                f"Скоро с вами свяжется менеджер для подтверждения.",
                parse_mode="HTML",
            )
        else:
            await callback.message.edit_text(
                "❌ <b>Не удалось перенести бронь.</b> Попробуйте позже.",
                parse_mode="HTML",
            )
        await callback.message.answer("Выберите действие в меню:", reply_markup=get_menu_keyboard())
        await callback.answer()
        return

    photosession_type = (nav_state or {}).get("current_type") or "studio"
    if photosession_type not in TYPE_NAMES:
        photosession_type = "studio"

    # Подготавливаем данные для сохранения заказа
    type_name = TYPE_NAMES.get(photosession_type, photosession_type)
    context_data = {
        "type": "booking",
        "photosession_type": photosession_type,
        "booking_date": target_date.strftime('%Y-%m-%d'),
        "booking_time": time,
        "notes": "",
    }

    success_message = (
        f"✅ <b>Бронь успешно создана!</b>\n\n"
        f"📸 {type_name}\n"
        f"📅 {target_date.strftime('%d.%m.%Y')}\n"
        f"⏰ {time}\n"
        f"📝 Статус: ожидает подтверждения\n\n"
        f"Скоро с вами свяжется менеджер для подтверждения."
    )

    # Запрашиваем номер телефона перед созданием заказа
    await request_phone_number(
        callback,
        state,
        context_data,
        success_message,
        is_callback=True,
        bot=bot,
    )

    # Очищаем состояние после запроса номера
    await db.clear_user_state(user_id)
    await callback.answer()


@calendar_router.callback_query(F.data.startswith("calblock_start_"))
async def admin_block_range_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_admin(user_id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    date_str = callback.data.split("_", 2)[2]
    await db.save_user_state(user_id, menu="block_range_start", type=date_str)

    target_date = booking_service.str_to_date(date_str)

    await callback.message.edit_text(
        f"🚫 <b>Закрытие дат</b>\n\n"
        f"Начало периода: <b>{target_date.strftime('%d.%m.%Y')}</b>\n\n"
        f"Теперь выберите в календаре дату окончания периода "
        f"(чтобы закрыть только один день — нажмите на ту же дату ещё раз).\n\n"
        f"«🔙 Назад» или «🏠 Меню» отменяют закрытие.",
        reply_markup=await _calendar_markup(user_id, target_date.year, target_date.month),
        parse_mode="HTML",
    )
    await callback.answer()


async def _finish_block_range(
    callback: CallbackQuery,
    nav_state: dict,
    end_date: date,
):
    user_id = callback.from_user.id
    start_date = booking_service.str_to_date(nav_state["current_type"])

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    dates = []
    d = start_date
    while d <= end_date:
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    await db.block_dates(dates, reason=f"Закрыто администратором {date.today().strftime('%d.%m.%Y')}")
    await db.clear_user_state(user_id)

    # Проверяем, нет ли на закрываемый период уже действующих броней —
    # их нужно решать вручную (связаться с клиентом/отменить самостоятельно).
    conflicts = []
    for d_str in dates:
        orders = await db.get_orders_by_date(d_str)
        conflicts.extend(orders)

    period_label = (
        start_date.strftime("%d.%m.%Y")
        if start_date == end_date
        else f"{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}"
    )

    text = f"🚫 <b>Закрыто для записи: {period_label}</b>\n\n"

    if conflicts:
        text += (
            f"⚠️ На этот период уже есть {len(conflicts)} действующих броней "
            f"— новые записи на эти даты запрещены, но старые остались как есть, "
            f"свяжитесь с клиентами:\n\n"
        )
        for order in conflicts:
            link = user_link(order["user_id"], order.get("username"), order.get("full_name"))
            text += (
                f"• #{order['order_id']} — {order['booking_date']} {order['booking_time']} — {link}"
                + (f" — 📞 {order['phone']}" if order.get("phone") else "")
                + "\n"
            )
    else:
        text += "Действующих броней на этот период нет."

    await callback.message.edit_text(
        text,
        reply_markup=await _calendar_markup(user_id, end_date.year, end_date.month),
        parse_mode="HTML",
    )
    await callback.answer("Даты закрыты 🚫")


@calendar_router.callback_query(F.data.startswith("calunblock_"))
async def admin_unblock_date(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_admin(user_id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    date_str = callback.data.split("_", 1)[1]
    await db.unblock_date(date_str)
    await callback.answer("Дата снова открыта ✅")

    target_date = booking_service.str_to_date(date_str)
    is_blocked = await db.is_date_blocked(date_str)
    bookings = await booking_service.get_bookings_by_date(user_id, target_date)
    booking = bookings[0] if bookings else None

    text = (
        f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n"
        f"{'-' * 20}\n"
        f"✅ Свободный день\n"
        f"{'-' * 20}\n"
        f"Хотите забронировать?"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_date_detail_keyboard(
            booking, target_date, is_admin=True, is_blocked=is_blocked
        ),
        parse_mode="HTML",
    )


@calendar_router.callback_query(F.data == "back_to_calendar")
async def back_to_calendar(callback: CallbackQuery):
    user_id = callback.from_user.id
    await db.clear_user_state(user_id)
    year, month = booking_service.get_current_month()

    await callback.message.edit_text(
        f"📅 <b>{MONTHS[month]} {year}</b>\n\n"
        f"Выберите дату для записи",
        reply_markup=await _calendar_markup(user_id, year, month),
        parse_mode="HTML",
    )
    await callback.answer()


@calendar_router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    await db.clear_user_state(user_id)
    await callback.message.delete()
    await callback.message.answer("Главное меню:", reply_markup=get_menu_keyboard())
    await callback.answer()


@calendar_router.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()