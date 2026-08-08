from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню"""
    builder = ReplyKeyboardBuilder()

    builder.row(
        KeyboardButton(text="📸 Выбрать фотосессию"),
        KeyboardButton(text="📅 Календарь")
    )
    builder.row(
        KeyboardButton(text="💰 Стоимость"),
        KeyboardButton(text="❓ Спросить")
    )

    return builder.as_markup(resize_keyboard=True)

menu_keyboard = get_menu_keyboard()