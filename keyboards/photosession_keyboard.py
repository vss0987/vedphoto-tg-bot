from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder


# клавиатура раздела Фотосессии

def get_photosession_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()

    builder.row(
        KeyboardButton(text="Студия"),
        KeyboardButton(text="Улица")
    )

    builder.row(
        KeyboardButton(text="Интерьер"),
        # KeyboardButton(text="Торжества")
    )

    builder.row(
        KeyboardButton(text="Назад")
    )

    return builder.as_markup(resize_keyboard=True)


photosession_keyboard = get_photosession_keyboard()
