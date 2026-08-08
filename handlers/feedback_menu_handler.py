import html
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import db
from constants import PRICES, PRICE_FROM
from keyboards.menu_keyboard import get_menu_keyboard
from handlers.admin_notify import notify_admin, user_link

logger = logging.getLogger(__name__)

feedback_router = Router()

# Тексты кнопок главного/фото-меню — используются, чтобы не принимать
# нажатия кнопок за текст вопроса.
MENU_BUTTON_TEXTS = {
    "⬅️ Назад",
    "📸 Выбрать фотосессию",
    "📅 Календарь",
    "💰 Стоимость",
    "❓ Спросить",
}


class QuestionStates(StatesGroup):
    waiting_for_question = State()


@feedback_router.message(F.text == "❓ Спросить")
async def ask_question_start(message: Message, state: FSMContext):
    """Обработчик кнопки 'Спросить'"""
    await state.set_state(QuestionStates.waiting_for_question)
    await message.answer(
        "📝 Напишите ваш вопрос текстом.\n\n"
        "Я передам его администратору.",
        reply_markup=get_menu_keyboard()
    )


@feedback_router.message(QuestionStates.waiting_for_question, F.text)
async def save_question(message: Message, state: FSMContext, bot: Bot):
    """Сохраняем вопрос (только в состоянии ожидания)"""
    text = message.text.strip()
    user = message.from_user

    # Проверяем, не кнопка ли это (игнорируем системные сообщения)
    if text in MENU_BUTTON_TEXTS:
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=get_menu_keyboard())
        return

    # Сохраняем вопрос
    display_username = user.username or f"@{user.first_name}"
    question_id = await db.save_question(
        user_id=user.id,
        username=display_username,
        text=text
    )

    await state.clear()

    if question_id:
        await message.answer(
            "✅ Ваш вопрос сохранён! Администратор ответит вам.",
            reply_markup=get_menu_keyboard()
        )
        link = user_link(user.id, user.username, user.full_name)
        await notify_admin(
            bot,
            f"❓ <b>Новый вопрос от {link}</b>\n\n{html.escape(text)}",
        )
    else:
        await message.answer(
            "⚠️ Ошибка при сохранении вопроса. Попробуйте позже.",
            reply_markup=get_menu_keyboard()
        )


@feedback_router.message(QuestionStates.waiting_for_question)
async def handle_non_text_in_question(message: Message, state: FSMContext):
    """Если пользователь отправил не текст, а что-то другое (фото, документ и т.д.)"""
    await message.answer(
        "Пожалуйста, напишите ваш вопрос текстом.",
        reply_markup=get_menu_keyboard()
    )


@feedback_router.message(F.text.in_(["💰 Стоимость"]))
async def show_prices(message: Message, state: FSMContext):
    """Показать стоимость фотосессий"""
    await state.clear()

    labels = {
        "studio": "🌟 <b>Студийная</b>",
        "street": "🌳 <b>Уличная</b>",
        "pets": "🐕 <b>С питомцами</b>",
        "celebration": "🎉 <b>Торжества</b>",
    }

    prices_text = "📸 <b>Стоимость фотосессий</b>\n\n"
    for key, label in labels.items():
        prefix = "от " if key in PRICE_FROM else ""
        price = f"{PRICES[key]:,}".replace(",", " ")
        prices_text += f"{label} — {prefix}{price} ₽\n"
    prices_text += "\n💳 Оплата на месте или по безналичному расчёту."

    await message.answer(
        prices_text,
        parse_mode="HTML",
        reply_markup=get_menu_keyboard()
    )


@feedback_router.message()
async def handle_other_messages(message: Message, state: FSMContext):
    """Обработка всех остальных текстовых сообщений (не кнопок)"""
    # Проверяем, находится ли пользователь в состоянии вопроса
    current_state = await state.get_state()
    if current_state == QuestionStates.waiting_for_question:
        # Если в состоянии вопроса, но сработал другой обработчик — игнорируем
        return

    # Игнорируем системные сообщения и кнопки
    text = message.text.strip() if message.text else ""
    if text in MENU_BUTTON_TEXTS:
        return

    # Если пользователь что-то написал не по кнопкам
    await message.answer(
        "Используйте кнопки меню для навигации.\n"
        "Если хотите задать вопрос — нажмите кнопку «❓ Спросить».",
        reply_markup=get_menu_keyboard()
    )