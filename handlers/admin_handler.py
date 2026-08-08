import logging
from datetime import datetime, date, timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from constants import TYPE_NAMES
from database import db
from handlers.admin_notify import is_admin, user_link

logger = logging.getLogger(__name__)
admin_router = Router()

STATUS_LABELS = {
    "pending": "⏳ Ожидает подтверждения",
    "confirmed": "✅ Подтверждён",
    "completed": "✔️ Выполнен",
    "cancelled": "❌ Отменён",
}

# Telegram режет сообщения длиннее 4096 символов — при большом количестве
# записей edit_text/answer упал бы с TelegramBadRequest. Подрезаем текст
# заранее и показываем сколько записей не поместилось.
TELEGRAM_TEXT_LIMIT = 3500


def _fmt_dt(value: str | None) -> str:
    """Дата/время в коротком читаемом виде: 'YYYY-MM-DD HH:MM'."""
    if not value:
        return "—"
    return value[:16].replace("T", " ")


def _cut(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Защита от превышения лимита длины сообщения Telegram (4096 символов)."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…список обрезан, показаны не все записи."


def back_to_admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад в админ-панель", callback_data="admin_back")
    return builder.as_markup()


async def show_admin_menu(message: Message):
    """Показывает админ-меню БЕЗ ПРОВЕРКИ ПРАВ (только для внутреннего использования,
    когда доступ уже проверен вызывающим хендлером)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Все заказы", callback_data="admin_orders"),
        InlineKeyboardButton(text="🆕 Новые заказы", callback_data="admin_new_orders")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton(text="💰 Выручка", callback_data="admin_revenue")
    )
    builder.row(
        InlineKeyboardButton(text="📝 Вопросы", callback_data="admin_questions"),
        InlineKeyboardButton(text="👤 Пользователи", callback_data="admin_users")
    )
    builder.row(
        InlineKeyboardButton(text="📅 Заказы на сегодня", callback_data="admin_today")
    )
    builder.row(
        InlineKeyboardButton(text="🚫 Закрытые даты", callback_data="admin_blocked_dates"),
        InlineKeyboardButton(text="📦 Архив взаимодействий", callback_data="admin_archive_now"),
    )
    builder.adjust(2)

    await message.answer(
        "⚙️ <b>Админ-панель</b>\n\n"
        "Выберите действие:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


def _order_block(order: dict) -> str:
    """Единый формат карточки заказа: статус, клиент, телефон, тип, даты."""
    status_label = STATUS_LABELS.get(order["status"], order["status"])
    type_name = TYPE_NAMES.get(order["photosession_type"], order["photosession_type"])
    link = user_link(order["user_id"], order.get("username"), order.get("full_name"))

    lines = [
        f"#{order['order_id']} · {status_label}",
        f"👤 {link}",
    ]
    if order.get("phone"):
        lines.append(f"📞 {order['phone']}")
    lines.append(f"📸 {type_name}")
    lines.append(f"📅 Запись на: {order.get('booking_date') or '—'} {order.get('booking_time') or ''}".rstrip())
    lines.append(f"🕐 Дата обращения: {_fmt_dt(order.get('created_at'))}")
    lines.append("—" * 20)
    return "\n".join(lines) + "\n"


def _order_actions_keyboard(orders: list[dict], origin: str) -> InlineKeyboardMarkup:
    """Клавиатура с быстрыми действиями по каждому заказу (подтвердить/
    завершить/отменить), плюс кнопка назад. origin ('new' | 'today') нужен,
    чтобы после действия обновить именно тот экран, с которого пришли."""
    builder = InlineKeyboardBuilder()
    action_rows = 0
    for order in orders:
        oid = order["order_id"]
        status = order["status"]
        if status == "pending":
            builder.button(text=f"✅ Подтвердить #{oid}", callback_data=f"admin_confirm_{oid}_{origin}")
            builder.button(text=f"❌ Отменить #{oid}", callback_data=f"admin_cancelorder_{oid}_{origin}")
            action_rows += 1
        elif status == "confirmed":
            builder.button(text=f"✔️ Завершить #{oid}", callback_data=f"admin_complete_{oid}_{origin}")
            builder.button(text=f"❌ Отменить #{oid}", callback_data=f"admin_cancelorder_{oid}_{origin}")
            action_rows += 1

    builder.button(text="⬅️ Назад в админ-панель", callback_data="admin_back")

    if action_rows:
        builder.adjust(*([2] * action_rows), 1)
    else:
        builder.adjust(1)

    return builder.as_markup()


@admin_router.message(Command("admin"))
async def admin_panel(message: Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("⛔ У вас нет доступа к админ-панели.")
        return
    await show_admin_menu(message)


@admin_router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await show_admin_menu(callback.message)
    await callback.answer()


# ============ ВСЕ ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ============

@admin_router.callback_query(F.data == "admin_orders")
async def admin_show_orders(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    orders = await db.get_all_orders(limit=50)

    if not orders:
        await callback.message.edit_text(
            "📋 <b>Все заказы</b>\n\n"
            "Пока нет ни одного заказа.",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    text = "📋 <b>Все заказы</b>\n\n"
    for order in orders:
        text += _order_block(order)

    text += f"\n📊 Показано заказов: <b>{len(orders)}</b>"

    await callback.message.edit_text(
        _cut(text),
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_new_orders")
async def admin_new_orders(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    orders = await db.get_orders_by_status("pending")

    if not orders:
        await callback.message.edit_text(
            "🆕 <b>Новые заказы</b>\n\n"
            "✨ Нет новых заказов. Отлично!",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    text = "🆕 <b>Новые заказы</b>\n\n"
    for order in orders:
        text += _order_block(order)

    text += f"\n📊 Новых заказов: <b>{len(orders)}</b>"

    await callback.message.edit_text(
        _cut(text),
        reply_markup=_order_actions_keyboard(orders, origin="new"),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_today")
async def admin_today_orders(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    today = datetime.now().strftime("%Y-%m-%d")
    orders = await db.get_orders_by_date(today)

    if not orders:
        await callback.message.edit_text(
            "📅 <b>Заказы на сегодня</b>\n\n"
            f"📆 {today}\n"
            "Сегодня нет заказов. Отдыхайте! 🎉",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    text = f"📅 <b>Заказы на сегодня</b>\n\n📆 {today}\n{'—' * 20}\n"
    for order in orders:
        text += _order_block(order)

    await callback.message.edit_text(
        _cut(text),
        reply_markup=_order_actions_keyboard(orders, origin="today"),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    total_orders = await db.get_orders_count()
    pending_orders = await db.get_orders_count(status="pending")
    confirmed_orders = await db.get_orders_count(status="confirmed")
    completed_orders = await db.get_orders_count(status="completed")
    cancelled_orders = await db.get_orders_count(status="cancelled")
    total_users = await db.get_users_count()
    total_questions = await db.get_questions_count()

    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👤 <b>Пользователи:</b> {total_users}\n"
        f"📋 <b>Всего заказов:</b> {total_orders}\n"
        f"   🆕 Новые: {pending_orders}\n"
        f"   ✅ Подтверждены: {confirmed_orders}\n"
        f"   ✔️ Выполнены: {completed_orders}\n"
        f"   ❌ Отменены: {cancelled_orders}\n"
        f"📝 <b>Вопросов:</b> {total_questions}\n"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_revenue")
async def admin_revenue(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    revenue = await db.get_revenue_stats()

    text = (
        "💰 <b>Выручка</b>\n\n"
        f"📆 За сегодня: <b>{revenue['today']} ₽</b>\n"
        f"📆 За неделю: <b>{revenue['week']} ₽</b>\n"
        f"📆 За месяц: <b>{revenue['month']} ₽</b>\n"
        f"📆 За всё время: <b>{revenue['total']} ₽</b>\n"
        "\n<i>Считаются заказы в статусе «подтверждён» и «выполнен»,\n"
        "по датам самой фотосессии. Цены — из справочника типов\n"
        "фотосессий, не из отдельного поля в заказе.</i>"
    )

    completed = await db.get_orders_count(status="completed")
    total = await db.get_orders_count()
    if total > 0:
        percent = round(completed / total * 100)
        text += f"\n✅ Выполнено заказов: <b>{percent}%</b>"

    await callback.message.edit_text(
        text,
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_questions")
async def admin_questions(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    questions = await db.get_all_questions(limit=30)

    if not questions:
        await callback.message.edit_text(
            "📝 <b>Вопросы</b>\n\n"
            "Нет новых вопросов.",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    text = "📝 <b>Вопросы</b>\n\n"
    for q in questions:
        # telegram_username — актуальный @username из users; username — то,
        # что было сохранено в момент вопроса (может быть просто "@Имя",
        # если публичного username у автора не было и нет до сих пор).
        link = user_link(
            q["user_id"],
            q.get("telegram_username"),
            q.get("full_name") or q.get("username"),
        )
        text += f"👤 {link}\n"
        if q.get("phone"):
            text += f"📞 {q['phone']}\n"
        text += (
            f"📝 {q['text'][:200]}{'...' if len(q['text']) > 200 else ''}\n"
            f"🕐 Дата обращения: {_fmt_dt(q.get('created_at'))}\n"
            f"{'—' * 20}\n"
        )

    await callback.message.edit_text(
        _cut(text),
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


def _parse_order_action(callback_data: str) -> tuple[int, str]:
    """'admin_confirm_42_new' -> (42, 'new')"""
    parts = callback_data.split("_")
    return int(parts[2]), parts[3] if len(parts) > 3 else "new"


async def _refresh_orders_screen(callback: CallbackQuery, origin: str):
    if origin == "today":
        await admin_today_orders(callback)
    else:
        await admin_new_orders(callback)


@admin_router.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_order(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    order_id, origin = _parse_order_action(callback.data)
    await db.update_order_status(order_id, "confirmed")
    await callback.answer("Заказ подтверждён ✅")
    await _refresh_orders_screen(callback, origin)


@admin_router.callback_query(F.data.startswith("admin_complete_"))
async def admin_complete_order(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    order_id, origin = _parse_order_action(callback.data)
    await db.update_order_status(order_id, "completed")
    await callback.answer("Заказ отмечен как выполненный ✔️")
    await _refresh_orders_screen(callback, origin)


@admin_router.callback_query(F.data.startswith("admin_cancelorder_"))
async def admin_cancel_order(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    order_id, origin = _parse_order_action(callback.data)
    await db.cancel_order(order_id)
    await callback.answer("Заказ отменён ❌")
    await _refresh_orders_screen(callback, origin)


def _group_consecutive_dates(blocked: list[dict]) -> list[dict]:
    """Группирует подряд идущие закрытые даты с одинаковой причиной в диапазоны,
    чтобы не показывать (и не открывать) каждый день по отдельности."""
    if not blocked:
        return []

    groups = []
    current = {
        "start": blocked[0]["booking_date"],
        "end": blocked[0]["booking_date"],
        "reason": blocked[0]["reason"],
    }
    prev_date = date.fromisoformat(blocked[0]["booking_date"])

    for item in blocked[1:]:
        d = date.fromisoformat(item["booking_date"])
        if d == prev_date + timedelta(days=1) and item["reason"] == current["reason"]:
            current["end"] = item["booking_date"]
        else:
            groups.append(current)
            current = {
                "start": item["booking_date"],
                "end": item["booking_date"],
                "reason": item["reason"],
            }
        prev_date = d

    groups.append(current)
    return groups


@admin_router.callback_query(F.data == "admin_blocked_dates")
async def admin_blocked_dates(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    blocked = await db.get_all_blocked_dates()

    if not blocked:
        await callback.message.edit_text(
            "🚫 <b>Закрытые даты</b>\n\n"
            "Нет закрытых дат — всё открыто для записи.\n\n"
            "Чтобы закрыть даты (отпуск, больничный и т.п.), откройте нужный "
            "день в 📅 Календаре — там появится кнопка «🚫 Закрыть период».",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    groups = _group_consecutive_dates(blocked)

    builder = InlineKeyboardBuilder()
    text = "🚫 <b>Закрытые даты</b>\n\n"
    for g in groups:
        label = g["start"] if g["start"] == g["end"] else f"{g['start']} — {g['end']}"
        reason = g["reason"] or "без причины"
        text += f"📅 {label} — {reason}\n"
        builder.button(
            text=f"✅ Открыть {label}",
            callback_data=f"admin_unblockrange_{g['start']}_{g['end']}",
        )

    builder.button(text="⬅️ Назад в админ-панель", callback_data="admin_back")
    builder.adjust(1)

    await callback.message.edit_text(
        _cut(text),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_unblockrange_"))
async def admin_unblock_range_from_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    _, _, start_date, end_date = callback.data.split("_", 3)
    await db.unblock_date_range(start_date, end_date)
    label = start_date if start_date == end_date else f"{start_date} — {end_date}"
    await callback.answer(f"Открыто: {label} ✅")
    await admin_blocked_dates(callback)


@admin_router.callback_query(F.data == "admin_archive_now")
async def admin_archive_now(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from handlers.interaction_logger import archive_interaction_log

    await callback.answer("Собираю архив…")
    result = await archive_interaction_log(callback.bot)

    if result is None:
        text = "📦 <b>Архив взаимодействий</b>\n\nЛог сейчас пуст — архивировать нечего."
    else:
        count, csv_path = result
        text = (
            "📦 <b>Архив взаимодействий</b>\n\n"
            f"Выгружено записей: <b>{count}</b>\n"
            f"Файл: <code>{csv_path}</code>\n\n"
            "Лог очищен, CSV отправлен в этот чат отдельным сообщением."
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML"
    )


@admin_router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    users = await db.get_all_users(limit=30)
    total = await db.get_users_count()

    if not users:
        await callback.message.edit_text(
            "👤 <b>Пользователи</b>\n\n"
            "Нет пользователей.",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    text = f"👤 <b>Пользователи</b>\n\nВсего: {total}\n{'—' * 20}\n"
    for user in users:
        link = user_link(user["user_id"], user.get("username"), user.get("full_name"))
        status = "🚫 Заблокирован" if user.get("is_blocked") else "✅ Активен"
        text += (
            f"🆔 {user['user_id']}\n"
            f"👤 {link}\n"
            f"📞 {user.get('phone') or '—'}\n"
            f"🕐 Дата обращения: {_fmt_dt(user.get('registered_at'))}\n"
            f"📌 Статус: {status}\n"
            f"{'—' * 20}\n"
        )

    await callback.message.edit_text(
        _cut(text),
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()
