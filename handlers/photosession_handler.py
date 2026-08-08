import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from constants import TYPE_NAMES
from services.booking_service import booking_service
from keyboards.menu_keyboard import get_menu_keyboard
from keyboards.photosession_keyboard import get_photosession_keyboard
from keyboards.calendar_keyboard import get_main_calendar_keyboard

photosession_router = Router()
logger = logging.getLogger(__name__)

REFERENCE_LINKS = {
    "studio": {
        "name": "Студия",
        "posts": [
            {"title": "Studio", "url": "https://t.me/your_channel/1"},
        ],
    },
    "street": {
        "name": "Улица",
        "posts": [
            {"title": "Street-фото", "url": "https://t.me/your_channel/3"},
        ],
    },
    "pets": {
        "name": "Питомцы",
        "posts": [
            {"title": "Фото с хвостиками", "url": "https://t.me/your_channel/5"},
        ],
    },
    "celebration": {
        "name": "Торжества",
        "posts": [
            {"title": "Торжества", "url": "https://t.me/your_channel/6"},
        ],
    },
}


@photosession_router.message(F.text == "📸 Выбрать фотосессию")
async def choose_photosession(message: Message):
    await message.answer(
        "Выберите тип фотосессии:",
        reply_markup=get_photosession_keyboard(),
    )


@photosession_router.message(F.text == "Студия")
async def show_studio_references(message: Message):
    await show_references(message, "studio")


@photosession_router.message(F.text == "Улица")
async def show_street_references(message: Message):
    await show_references(message, "street")


@photosession_router.message(F.text == "Питомцы")
async def show_pets_references(message: Message):
    await show_references(message, "pets")


@photosession_router.message(F.text == "Торжества")
async def show_celebration_references(message: Message):
    await show_references(message, "celebration")


async def show_references(message: Message, photosession_type: str):
    ref_data = REFERENCE_LINKS.get(photosession_type)
    if not ref_data:
        await message.answer("Извините, референсы временно недоступны")
        return

    builder = InlineKeyboardBuilder()
    for post in ref_data["posts"]:
        builder.button(text=f"📸 {post['title']}", url=post["url"])

    builder.button(
        text="📝 Заказать фотосессию",
        callback_data=f"order_{photosession_type}",
    )
    builder.button(text="⬅️ Назад", callback_data="back_to_photosession_menu")
    builder.adjust(1)

    await message.answer(
        f"📸 <b>{ref_data['name']}</b>\n\n"
        f"Нажми на понравившийся референс, чтобы посмотреть фото в канале\n"
        f"<i>В канале включена защита от скриншотов 🔒</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@photosession_router.callback_query(F.data.startswith("order_"))
async def order_photosession(callback: CallbackQuery):
    _, photosession_type = callback.data.split("_", 1)
    user_id = callback.from_user.id

    if photosession_type not in TYPE_NAMES:
        await callback.answer("Неизвестный тип фотосессии", show_alert=True)
        return

    await db.save_user_state(user_id, menu="booking", type=photosession_type)
    year, month = booking_service.get_current_month()
    booking_days = await booking_service.get_user_booking_days(user_id, year, month)
    type_name = TYPE_NAMES[photosession_type]

    await callback.message.answer(
        f"📅 <b>Бронирование: {type_name}</b>\n\n"
        f"Выберите удобную дату в календаре:",
        reply_markup=get_main_calendar_keyboard(year, month, booking_days),
        parse_mode="HTML",
    )
    await callback.message.delete()
    await callback.answer()


@photosession_router.callback_query(F.data == "back_to_photosession_menu")
async def back_to_photosession_menu(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "Выберите тип фотосессии:",
        reply_markup=get_photosession_keyboard(),
    )
    await callback.answer()


@photosession_router.message(F.text == "Назад")
async def back_to_menu(message: Message):
    await message.answer(
        "Главное меню:",
        reply_markup=get_menu_keyboard(),
    )
