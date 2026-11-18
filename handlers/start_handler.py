from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from database import db
from keyboards.menu_keyboard import get_menu_keyboard

start_router = Router()


@start_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = message.from_user

    # Добавляем пользователя в БД
    await db.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    # Записываем в историю
    await db.add_interaction(user.id, "/start")

    # Если пользователь был заблокирован, сбрасываем флаг
    await db.update_block_status(user.id, False)

    await state.clear()

    await message.answer(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу выбрать фотосессию и забронировать дату!\n\n"
        "• <b>📸 Выбрать фотосессию</b> - примеры работ и цены\n"
        "• <b>📅 Календарь</b> - свободные даты и время\n"
        "• <b>💰 Стоимость</b> - если хотите сначала узнать цены\n"
        "• <b>❓ Спросить</b> - если остались вопросы\n\n"
        "<b>С чего начнём?</b>",
        reply_markup=get_menu_keyboard()
    )


@start_router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Главное меню:",
        reply_markup=get_menu_keyboard()
    )