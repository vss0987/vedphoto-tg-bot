from aiogram import Router, F, Bot
from aiogram.types import Message, Contact
from aiogram.fsm.context import FSMContext

from database import db
from keyboards.menu_keyboard import get_menu_keyboard
from handlers.phone_required import finalize_booking

contact_router = Router()


@contact_router.message(F.contact)
async def handle_contact(message: Message, state: FSMContext, bot: Bot):
    contact: Contact = message.contact
    user = message.from_user

    # Проверяем, что номер отправлен именно этим пользователем
    if contact.user_id != user.id:
        await message.answer(
            "⚠️ Пожалуйста, отправьте свой номер телефона, а не чужой."
        )
        return

    await db.update_phone(user.id, contact.phone_number)
    await db.add_interaction(user.id, f"Отправлен номер телефона: {contact.phone_number}")

    data = await state.get_data()
    pending_booking = data.get("pending_booking")
    pending_success_message = data.get("pending_success_message")
    await state.clear()

    if pending_booking:
        # Пользователь делился номером в рамках оформления брони —
        # завершаем бронирование, для которого номер и запрашивался.
        await finalize_booking(
            message.answer,
            user.id,
            pending_booking,
            pending_success_message or "✅ Бронь успешно создана!",
            bot=bot,
        )
    else:
        await message.answer(
            "✅ Спасибо! Ваш номер телефона сохранён.\n"
            "Теперь вы можете записаться на фотосессию.",
            reply_markup=get_menu_keyboard(),
        )
