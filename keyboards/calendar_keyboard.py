from datetime import date
from typing import List, Set

import calendar as cal
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from constants import TYPE_NAMES


def get_main_calendar_keyboard(
    year: int,
    month: int,
    booking_days: Set[int] | None = None,
    blocked_days: Set[int] | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура календаря. Дни с бронями помечаются точкой,
    закрытые администратором дни — 🚫."""
    builder = InlineKeyboardBuilder()
    booking_days = booking_days or set()
    blocked_days = blocked_days or set()

    # Кнопка просмотра записей
    builder.button(
        text="📝 Записи",
        callback_data="show_my_bookings",
    )

    month_cal = cal.monthcalendar(year, month)
    today = date.today()

    # Календарь
    for week in month_cal:
        for day in week:
            if day == 0:
                builder.button(
                    text=" ",
                    callback_data="ignore",
                )
            else:
                current_date = date(year, month, day)

                # Прошедшие даты
                if current_date < today:
                    builder.button(
                        text=f"·{day}",
                        callback_data="ignore",
                    )
                elif day in blocked_days:
                    builder.button(
                        text=f"🚫{day}",
                        callback_data=f"date_{year}_{month}_{day}",
                    )
                else:
                    label = (
                        f"•{day}"
                        if day in booking_days
                        else str(day)
                    )

                    builder.button(
                        text=label,
                        callback_data=f"date_{year}_{month}_{day}",
                    )

    prev_month, prev_year = (
        (12, year - 1)
        if month == 1
        else (month - 1, year)
    )

    next_month, next_year = (
        (1, year + 1)
        if month == 12
        else (month + 1, year)
    )

    # Нижняя навигация
    builder.button(
        text="◀️",
        callback_data=f"month_{prev_year}_{prev_month}",
    )

    builder.button(
        text="🏠 Меню",
        callback_data="back_to_menu",
    )

    builder.button(
        text="▶️",
        callback_data=f"month_{next_year}_{next_month}",
    )

    # 1 строка — Записи
    # N строк по 7 кнопок — календарь
    # 1 строка — навигация
    builder.adjust(
        1,
        *([7] * len(month_cal)),
        3,
    )

    return builder.as_markup()


def get_session_type_keyboard(
    date_str: str,
) -> InlineKeyboardMarkup:
    """Выбор типа фотосессии перед бронированием."""
    builder = InlineKeyboardBuilder()

    for key, name in TYPE_NAMES.items():
        builder.button(
            text=name,
            callback_data=f"picktype_{date_str}_{key}",
        )

    builder.button(
        text="🔙 Назад",
        callback_data="back_to_calendar",
    )

    builder.adjust(2, 2, 1)

    return builder.as_markup()


def get_date_detail_keyboard(
    booking,
    target_date: date,
    *,
    is_admin: bool = False,
    is_blocked: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    date_str = target_date.strftime("%Y-%m-%d")
    row_sizes = []

    if booking:
        builder.button(
            text="📝 Перенести",
            callback_data=f"reschedule_start_{booking.order_id}",
        )

        builder.button(
            text="❌ Отменить",
            callback_data=f"cancel_{booking.order_id}",
        )

        row_sizes.append(2)
    elif not is_blocked:
        builder.button(
            text="📝 Забронировать",
            callback_data=f"book_{date_str}",
        )
        row_sizes.append(1)

    if is_admin:
        if is_blocked:
            builder.button(
                text="✅ Открыть эту дату",
                callback_data=f"calunblock_{date_str}",
            )
        else:
            builder.button(
                text="🚫 Закрыть период с этой даты",
                callback_data=f"calblock_start_{date_str}",
            )
        row_sizes.append(1)

    builder.button(
        text="🔙 Назад",
        callback_data="back_to_calendar",
    )
    row_sizes.append(1)

    builder.adjust(*row_sizes)

    return builder.as_markup()


def get_time_slots_keyboard(
    target_date: date,
    available_slots: List[str],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    date_str = target_date.strftime("%Y-%m-%d")

    for slot in available_slots:
        builder.button(
            text=f"🕐 {slot}",
            callback_data=f"time_{date_str}_{slot.replace(':', '')}",
        )

    builder.button(
        text="🔙 Назад",
        callback_data="back_to_calendar",
    )

    # 3 кнопки времени в ряд + отдельная строка для "Назад"
    full_rows, remainder = divmod(len(available_slots), 3)
    row_sizes = [3] * full_rows
    if remainder:
        row_sizes.append(remainder)
    row_sizes.append(1)  # строка "Назад"

    builder.adjust(*row_sizes)

    return builder.as_markup()


def get_booking_list_keyboard(
    bookings: List,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    active_bookings = [
        booking
        for booking in bookings
        if booking.status != "cancelled"
    ]

    for booking in active_bookings:
        date_str = (
            booking.date.strftime("%d.%m")
            if booking.date
            else "—"
        )

        builder.button(
            text=f"📅 {date_str} • {booking.time}",
            callback_data=f"booking_{booking.order_id}",
        )

    if active_bookings:
        builder.adjust(1)

    builder.button(
        text="🔙 Назад",
        callback_data="back_to_calendar",
    )

    builder.adjust(1)

    return builder.as_markup()