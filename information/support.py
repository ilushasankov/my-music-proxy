import logging
from aiogram import Router, types
from aiogram.types import LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext  
from aiogram.filters import Command
from .states import DonationStates

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(lambda c: c.data == "donate")
async def donate_handler(callback: types.CallbackQuery):
    """Обрабатывает нажатие кнопки 'Поддержать'."""
    try:
        await callback.answer()
        # В групповых чатах функция доната не работает, отправим уведомление
        if callback.message.chat.type != "private":
            await callback.message.answer(
                "👀Функция поддержки доступна только в личных сообщениях с ботом.",
                parse_mode="HTML"
            )
            return
            
        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(text="5 ⭐", callback_data="donate_stars_5"))
        builder.add(types.InlineKeyboardButton(text="25 ⭐", callback_data="donate_stars_25"))
        builder.add(types.InlineKeyboardButton(text="50 ⭐", callback_data="donate_stars_50"))
        builder.add(types.InlineKeyboardButton(text="100 ⭐", callback_data="donate_stars_100"))
        builder.row(types.InlineKeyboardButton(text="Свое количество ⭐", callback_data="donate_stars_custom"))
        
        await callback.message.answer(
            "🌟<b>ПОДДЕРЖАТЬ РАЗВИТИЕ БОТА</b>\n\n"
            "💳<b>Переводом на карту</b>\n\n"
            "— <b>Т-Банк</b> 5536914008008074\n"
            "— <b>Сбер</b> 4276671200918792\n"
            "— <b>Яндекс Банк</b> 2204310110203471\n"
            "— <b>Газпромбанк</b> 4249170367470691\n\n"
            "Получатель: <b>Илья Саньков</b>\n"
            "<blockquote>Также вы можете поддержать нас, подарив <b>подарок за звезды</b> или поставив <b>звездные реакции</b> под <b>любым</b> постом в нашем канале @chedmemes!</blockquote>\n\n"
            "⭐️<b>Звездами</b>\n\n",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.exception(f"Ошибка при отправке сообщения поддержки: {e}")
        await callback.answer("Ошибка при отправке сообщения", show_alert=True)

@router.message(F.text.casefold() == "отмена", DonationStates.waiting_for_amount)
@router.message(Command("cancel"), DonationStates.waiting_for_amount)
async def cancel_donation_handler(message: types.Message, state: FSMContext):
    """
    Обрабатывает отмену ввода суммы пожертвования.
    Ловит команду /cancel или текст "отмена", только когда пользователь в состоянии ожидания.
    """
    await state.clear()
    await message.answer(
        "Действие отменено👌",
        reply_markup=types.ReplyKeyboardRemove()
    )

@router.callback_query(lambda c: c.data.startswith("donate_stars_"))
async def donate_stars_handler(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор количества звезд для пожертвования."""
    try:
        await callback.answer()
        data = callback.data.split("_")

        if data[2] == "custom":
            kb = types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="Отмена")]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await callback.message.answer(
                "✨Введи количество звезд, которое хочешь пожертвовать (от 1 до 10000)\n\n"
                "Чтобы отменить, введи /cancel или нажми кнопку ниже👇",
                reply_markup=kb 
            )
            await state.set_state(DonationStates.waiting_for_amount)
            return

        amount = int(data[2])
        prices = [LabeledPrice(label="Поддержка бота", amount=amount)]
        await callback.message.answer_invoice(
            title="Поддержать развитие бота",
            description="Спасибо за твою поддержку💖",
            payload="donate_payload",
            provider_token="", # Убедитесь, что здесь есть токен, если вы хотите реальные платежи
            currency="XTR",
            prices=prices,
            start_parameter="donate"
        )
    except Exception as e:
        logger.exception(f"Ошибка при отправке invoice: {e}")
        await callback.message.answer("🚫Произошла ошибка при попытке поддержать бота")


@router.message(DonationStates.waiting_for_amount, F.text.isdigit())
async def custom_stars_handler(message: types.Message, state: FSMContext):
    """Обрабатывает пользовательский ввод количества звезд, когда бот в состоянии ожидания."""
    try:
        amount = int(message.text)
        if not (1 <= amount <= 10000):
            await message.answer("❌Пожалуйста, введи число от 1 до 10000")
            return

        # 4. ДОБАВЛЯЕМ УДАЛЕНИЕ КЛАВИАТУРЫ ПОСЛЕ УСПЕШНОГО ВВОДА
        await message.answer("Отлично! Создаю счет...", reply_markup=types.ReplyKeyboardRemove())

        prices = [LabeledPrice(label="Поддержка бота", amount=amount)]
        await message.answer_invoice(
            title="Поддержать развитие бота",
            description="Спасибо за твою поддержку💖",
            payload="donate_payload",
            provider_token="", # Убедитесь, что здесь есть токен
            currency="XTR",
            prices=prices,
            start_parameter="donate"
        )
        # ОЧЕНЬ ВАЖНО: выходим из состояния после успешной обработки
        await state.clear()

    except (ValueError, TypeError):
         await message.reply("Пожалуйста, введи корректное число...")
         return # Остаемся в том же состоянии
    except Exception as e:
        logger.exception(f"Ошибка при обработке ввода количества звезд: {e}")
        await message.answer("🚫Произошла ошибка при обработке ввода")
        # Выходим из состояния в случае непредвиденной ошибки
        await state.clear()


@router.message(DonationStates.waiting_for_amount)
async def incorrect_custom_stars_input(message: types.Message):
    """Ловит любой некорректный ввод (не цифры) в состоянии ожидания доната."""
    await message.answer("Пожалуйста, введи сумму числом или отмени действие, написав /cancel😥")

@router.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: types.PreCheckoutQuery):
    """Подтверждает запрос перед оплатой."""
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    """Обрабатывает успешную оплату."""
    await message.answer("🎉Спасибо за твою поддержку! Ты помогаешь боту становиться лучше!")