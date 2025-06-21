from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
import logging
import os
from download_functions import limitations
logger = logging.getLogger(__name__)
router = Router()

DEFAULT_TEXT = (
    "<b>ИНФОРМАЦИЯ О БОТЕ</b>\n\n"
    "Ищу и скачиваю треки из разных источников\n"
    "<blockquote>Для более точного поиска лучше указывать название без ошибок. Например, «Без названия - Неизвестен»🤔</blockquote>\n\n"
    "👾<b>Лимиты и возможности:</b>\n"
    "• С подпиской на @chedmemes: {base_limit} скачиваний в сутки\n"
    "• С подпиской на @chedmemes и @djilusha:  {premium_limit} скачиваний и приоритет в очереди!\n\n"
    "💬Для сотрудничества и жалоб: @ilushasankov_bot\n\n"
    "✨<b>Если тебе нравится бот, ты можешь поддержать его развитие!</b>"
).format(
    base_limit=limitations.BASE_DOWNLOAD_LIMIT,
    premium_limit=limitations.PREMIUM_DOWNLOAD_LIMIT
)


ONAS_TEXT = (
            "<b>ЧЭД</b> — мемы, рецензии, опросы, новости и другой контент, связанный с электронной музыкой. <a href='https://telegra.ph/CHEHD-03-09'>Подробнее о нас...</a>\n\n"
            "🌐<a href='https://t.me/ilushasankov_bot'>Реклама</a> / <a href='https://t.me/addlist/bu98CQLVAiViZWJi'>другие проекты</a>\n\n"
            "<b>НАШИ РЕСУРСЫ&#128279</b>\n"
            "<a href='https://t.me/chedmemes'>Telegram</a> | "
            "<a href='https://youtube.com/@chedmemes'>YouTube</a> | "
            "<a href='https://www.twitch.tv/chedstreams'>Twitch</a> | "
            "<a href='https://discord.gg/8tvUERGqmk'>Discord</a>\n\n"
            "<b>БЕСЕДЫ&#128172</b>\n"
            "<a href='https://t.me/chedmemeschat'>Чат в Telegram</a>\n\n"
            "<b>РАЗНОЕ&#128171</b>\n"
            "<a href='https://t.me/addstickers/chedmemes'>Стикеры</a> | "
            "<a href='https://t.me/addstickers/chedmemespack'>видеостикеры</a> | "
            "<a href='https://t.me/addemoji/chedmemesemoji'>эмодзи</a>\n\n"
            "<b>АДМИНИСТРАЦИЯ&#128019</b>\n"
            "<a href='https://t.me/djilusha'>Создатель</a> | "
            "<a href='https://t.me/Jukedubz'>заместитель</a>"
)

def get_default_keyboard() -> types.InlineKeyboardMarkup:
    """Создаёт клавиатуру для основной страницы информации."""
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🐔О нас", callback_data="info_show_onas"))
    builder.add(types.InlineKeyboardButton(text="✨Поддержать", callback_data="donate"))
    return builder.as_markup()

def get_onas_keyboard() -> types.InlineKeyboardMarkup:
    """Создаёт клавиатуру для страницы 'О нас'."""
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🔙Назад", callback_data="info_show_default"))
    builder.add(types.InlineKeyboardButton(text="✨Поддержать", callback_data="donate"))
    return builder.as_markup()

@router.message(Command("info"))
async def info_handler(message: types.Message):
    """Обрабатывает команду /info и отправляет основную информацию."""
    try:
        # Убедитесь, что картинка лежит по этому пути
        photo_path = "information/pictures/info_photo.png"
        photo = FSInputFile(photo_path)
        await message.answer_photo(
            photo=photo,
            caption=DEFAULT_TEXT,
            parse_mode="HTML",
            reply_markup=get_default_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения /info: {e}")
        # Если картинка не найдена, отправим просто текст
        await message.answer(
            text=DEFAULT_TEXT,
            parse_mode="HTML",
            reply_markup=get_default_keyboard()
        )

@router.callback_query(lambda c: c.data == "info_show_onas")
async def show_onas_handler(callback: types.CallbackQuery):
    """Показывает страницу 'О нас'."""
    try:
        await callback.message.edit_caption(
            caption=ONAS_TEXT,
            parse_mode="HTML",
            reply_markup=get_onas_keyboard()
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Ошибка при переходе на страницу 'О нас': {e}")
        await callback.answer("Ошибка при переключении страницы", show_alert=True)

@router.callback_query(lambda c: c.data == "info_show_default")
async def show_default_handler(callback: types.CallbackQuery):
    """Возвращает основную страницу информации."""
    try:
        await callback.message.edit_caption(
            caption=DEFAULT_TEXT,
            parse_mode="HTML",
            reply_markup=get_default_keyboard()
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Ошибка при возврате к основной странице: {e}")
        await callback.answer("Ошибка при переключении страницы", show_alert=True)