from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_feedback_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура раздела Обратной связи"""
    builder = ReplyKeyboardBuilder()

    builder.row(
        KeyboardButton(text="💰 Стоимость фотосессии"),
        KeyboardButton(text="✍️ Задать вопрос")
    )

    builder.row(
        KeyboardButton(text="⬅️ Назад")
    )

    return builder.as_markup(resize_keyboard=True)

feedback_keyboard = get_feedback_keyboard()