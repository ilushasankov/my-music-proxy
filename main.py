import asyncio
import os
import hashlib
import re
import time
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup
from aiogram.enums import ChatType
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv

from thefuzz import fuzz

import download_functions.limitations as limitations
from download_functions.yt_download import search_tracks_optimized
from download_functions.saavn_api import search_tracks_saavn
from download_functions.yandex_music_api import search_tracks_yandex, init_yandex_music_client

from download_functions.database import (
    init_db, get_cached_search, save_search_to_cache,
    save_soundcloud_url, get_soundcloud_url,
    cleanup_expired_cache, cleanup_expired_soundcloud_urls
)
from download_functions.soundcloud_api import search_tracks_soundcloud
from information import info, support

load_dotenv()
API_TOKEN = os.getenv('TELEGRAM_TOKEN')
if not API_TOKEN:
    raise ValueError("Необходимо установить TELEGRAM_TOKEN в .env файле")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
main_router = Router() 
    
PAGE_SIZE = 5

MAX_DURATION_SECONDS = 900
RELEVANCE_THRESHOLD = 60
HIGH_CONFIDENCE_THRESHOLD = 88

def create_query_hash(query: str) -> str:
    """Создает MD5 хэш из запроса для использования в качестве ключа."""
    return hashlib.md5(query.lower().encode()).hexdigest()

def filter_tracks_by_duration(tracks: list) -> list:
    """Фильтрует треки по длительности, исключая слишком длинные."""
    filtered_tracks = []
    for track in tracks:
        duration = track.get('duration', 0)
        if 0 < duration <= MAX_DURATION_SECONDS:
            filtered_tracks.append(track)
    return filtered_tracks

def score_tracks(query: str, tracks: list) -> list:
    """Оценивает релевантность каждого трека из списка."""
    scored_tracks = []
    normalized_query = query.lower()
    for track in tracks:
        artist = track.get('artist', '')
        title = track.get('title', '')
        track_string_for_comparison = f"{artist} {title}".lower()
        relevance_score = fuzz.token_set_ratio(normalized_query, track_string_for_comparison)
        track['relevance_score'] = relevance_score
        scored_tracks.append(track)
    return scored_tracks

def merge_and_sort_results(query: str, all_results: list) -> list:
    """
    Объединяет результаты, вычисляет релевантность, фильтрует, удаляет дубликаты и сортирует.
    """
    source_priority = {'yandex': 3, 'saavn': 2, 'soundcloud': 2, 'yt': 1}
    scored_tracks = score_tracks(query, all_results)

    final_candidates = [
        track for track in scored_tracks
        if track['relevance_score'] >= RELEVANCE_THRESHOLD
    ]
    for track in final_candidates:
        track['source_priority'] = source_priority.get(track.get('source'), 0)

    unique_tracks = {}
    for track in final_candidates:
        artist_norm = re.sub(r'[\(\[].*?[\)\]]', '', track.get('artist', '')).strip().lower()
        title_norm = re.sub(r'[\(\[].*?[\)\]]', '', track.get('title', '')).strip().lower()
        unique_key = f"{artist_norm} - {title_norm}"

        if (unique_key not in unique_tracks or
                track['relevance_score'] > unique_tracks[unique_key]['relevance_score'] or
                (track['relevance_score'] == unique_tracks[unique_key]['relevance_score'] and
                 track['source_priority'] > unique_tracks[unique_key]['source_priority'])):
            unique_tracks[unique_key] = track

    final_list = list(unique_tracks.values())
    final_list.sort(key=lambda x: (x['relevance_score'], x['source_priority']), reverse=True)
    return final_list

async def get_paginated_keyboard(results: list, query_hash: str, page: int = 0):
    builder = InlineKeyboardBuilder()
    start_offset = page * PAGE_SIZE
    end_offset = start_offset + PAGE_SIZE

    for track in results[start_offset:end_offset]:
        source = track.get('source', 'yt')
        icon_map = {'yandex': '💛', 'saavn': '💛', 'soundcloud': '☁️', 'yt': '📮'}
        source_icon = icon_map.get(source, '🎧')

        duration = track.get('duration')
        duration_str = f" ({int(duration) // 60}:{int(duration) % 60:02d})" if duration else ""

        artist = track.get('artist')
        title = track.get('title', 'Unknown Title')

        button_text = f"{source_icon}{duration_str} {artist} - {title}" if artist else f"{source_icon}{duration_str} {title}"
        if len(button_text) > 60:
            button_text = button_text[:57] + "..."

        callback_data = None
        track_id = track.get('id')
        track_url = track.get('url')

        if source == 'soundcloud':
            if track_url:
                potential_data = f"dl:soundcloud:{track_url}"
                if len(potential_data.encode('utf-8')) <= 64:
                    callback_data = potential_data
                else:
                    url_hash = hashlib.md5(track_url.encode()).hexdigest()[:12]
                    callback_data = f"dl:sc:{url_hash}"
                    # ИЗМЕНЕНИЕ: Сохраняем в БД вместо словаря
                    await save_soundcloud_url(url_hash, track_url)
        else:
            if track_id:
                potential_data = f"dl:{source}:{track_id}"
                if len(potential_data.encode('utf-8')) <= 64:
                    callback_data = potential_data

        if callback_data:
            builder.button(text=button_text, callback_data=callback_data)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="👈", callback_data=f"page:{page-1}:{query_hash}"))
    if end_offset < len(results):
        nav_buttons.append(InlineKeyboardButton(text="👉", callback_data=f"page:{page+1}:{query_hash}"))

    builder.adjust(1)
    if nav_buttons:
        builder.row(*nav_buttons)

    return builder.as_markup()

@main_router.message(F.chat.type == ChatType.PRIVATE, F.text == "/start")
async def cmd_start(message: Message):
    is_subscribed = await limitations.check_subscription(bot, message.from_user.id, limitations.CHANNEL_ID_1)
    if not is_subscribed:
         await message.answer(f"Привет!\n\nДля использования бота нужно подписаться на наш канал: {limitations.CHANNEL_ID_1}\n\n")
         return
    await message.answer(
        "Привет!\n\n"
        "Отправь мне название, и я найду что-нибудь для тебя🫡\n\n"
        "<blockquote>💛 <b>— треки лучшего качества</b>\n"
        "☁️ <b>— треки с SoundCloud</b>\n"
        "📮 <b>— треки с YouTube</b></blockquote>\n\n"
        "◽️Больше информации в разделе /info",
        parse_mode='HTML'
    )


@main_router.message(F.chat.type == ChatType.PRIVATE, F.text, ~F.text.startswith('/'))
async def handle_query(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if not await limitations.check_subscription(bot, message.from_user.id, limitations.CHANNEL_ID_1):
        await message.reply("Для использования бота необходимо подписаться на канал🤨")
        return

    error_message = limitations.check_search_rate_limit(message.from_user.id)
    if error_message:
        await message.reply(error_message); return

    query = message.text.strip()
    if not query or len(query) < 2:
        await message.reply("Минимум 2 символа🤨"); return

    query_hash = create_query_hash(query)
    status_message = await message.reply("🔎 Ищу треки...")

    cached_results = await get_cached_search(query_hash)
    if cached_results:
        filtered_cached = filter_tracks_by_duration(cached_results)
        if filtered_cached:
            print(f"Found in DB cache: {query}")
            keyboard = await get_paginated_keyboard(filtered_cached, query_hash, page=0)
            await status_message.edit_text(f"🎧 Найдено {len(filtered_cached)} композиции. Выбери:", reply_markup=keyboard)
            return

    try:
        await status_message.edit_text("🔎 Ищу треки во всех источниках...")

        tasks = [
            search_tracks_yandex(query, limit=5),
            search_tracks_saavn(query, limit=5),
            search_tracks_optimized(query, limit=10),
            search_tracks_soundcloud(query, limit=10),
        ]
        
        primary_term = query.split()[0]
        if len(query.split()) > 1 and len(primary_term) > 2:
             print(f"Hybrid search: using '{primary_term}' as a potential artist.")
             tasks.extend([
                search_tracks_yandex(primary_term, limit=10),
                search_tracks_saavn(primary_term, limit=10)
             ])
        
        all_raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        combined_all_candidates = []
        seen_unique_ids = set()
        for result_list in all_raw_results:
            if isinstance(result_list, list):
                for track in result_list:
                    track_unique_id = f"{track.get('source', 'unknown')}_{track.get('id', 'unknown')}"
                    if track_unique_id not in seen_unique_ids:
                        combined_all_candidates.append(track)
                        seen_unique_ids.add(track_unique_id)

        final_results = merge_and_sort_results(query, combined_all_candidates)
        final_filtered_results = filter_tracks_by_duration(final_results)

        if not final_filtered_results:
            await status_message.edit_text("❌ Трек не найден. Попробуй другой запрос или измени его (например, убери лишние слова).")
            return

        await save_search_to_cache(query_hash, final_filtered_results)
        keyboard = await get_paginated_keyboard(final_filtered_results, query_hash, page=0)

        duration_info = f"⏱️ Показаны только треки до 15 минут.\n" if len(final_filtered_results) < len(final_results) else ""
        await status_message.edit_text(f"🎧 Найдено {len(final_filtered_results)} композиции. Выбери для скачивания:\n{duration_info}", reply_markup=keyboard)

    except Exception as e:
        await status_message.edit_text("❌ Ошибка поиска. Попробуй позже.")
        print(f"Search error for query '{query}': {e}", exc_info=True)


@main_router.callback_query(F.message.chat.type == ChatType.PRIVATE, F.data.startswith("page:"))
async def handle_pagination(call: CallbackQuery):
    await call.answer()

    try:
        _, page_str, query_hash = call.data.split(":", 2)
        page = int(page_str)

        results = await get_cached_search(query_hash)

        if not results:
            await call.answer("Результаты поиска устарели, выполни поиск заново.", show_alert=True)
            await call.message.delete()
            return

        filtered_results = filter_tracks_by_duration(results)

        if not filtered_results or page * PAGE_SIZE >= len(filtered_results):
            await call.answer("На следующих страницах нет подходящих треков.", show_alert=True)
            return
        
        keyboard = await get_paginated_keyboard(filtered_results, query_hash, page=page)
        await call.message.edit_reply_markup(reply_markup=keyboard)

    except (ValueError, IndexError) as e:
        await call.answer("Ошибка навигации.", show_alert=True)
        print(f"Pagination error: {e}, Data: {call.data}")


@main_router.callback_query(F.message.chat.type == ChatType.PRIVATE, F.data.startswith("dl:"))
async def handle_download(call: CallbackQuery):
    user_id = call.from_user.id

    try:
        _, source, track_identifier = call.data.split(":", 2)
        track_id = track_identifier # По умолчанию

        if source == 'sc':
            full_url = await get_soundcloud_url(track_identifier)
            if full_url:
                source = 'soundcloud' # Меняем источник на правильный для воркера
                track_id = full_url   # Передаем полную ссылку
            else:
                await call.answer("Ссылка на трек устарела. Пожалуйста, выполните поиск заново.", show_alert=True)
                return

    except ValueError:
        await call.answer("Ошибка: неверный формат данных для скачивания.", show_alert=True)
        return

    can_download, message, _queue_pos, is_premium = await limitations.check_all_limits(bot, user_id, source)

    if not can_download:
        if "Чтобы увеличить лимит" in message and limitations.CHANNEL_ID_2:
            channel = limitations.CHANNEL_ID_2.lstrip('@')
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✨Подписаться", url=f"https://t.me/{channel}")]])
            await call.message.answer(message, reply_markup=kb)
        else:
            await call.answer(message, show_alert=True)
        return

    limitations.downloading_users.add(user_id)
    await call.answer()
    await call.message.edit_text(message)

    priority = limitations.PRIORITY_PREMIUM if is_premium else limitations.PRIORITY_NORMAL
    task_item = (priority, time.time(), (call, source, track_id))

    if source == 'yandex':
        await limitations.slow_queue.put(task_item)
    else:
        await limitations.fast_queue.put(task_item)

async def start_periodic_db_cleanup():
    """Запускает бесконечный цикл для очистки устаревших записей в БД."""
    while True:
        await asyncio.sleep(3600) # Проверять каждый час
        print("⏳ Запуск периодической очистки БД...")
        try:
            await cleanup_expired_cache()
            await cleanup_expired_soundcloud_urls()
        except Exception as e:
            print(f"❌ Ошибка во время периодической очистки БД: {e}")

async def on_startup(bot_instance: Bot):
    await init_db()
    await init_yandex_music_client()

    num_fast_workers = int(os.getenv('FAST_WORKERS', 6))
    num_slow_workers = int(os.getenv('SLOW_WORKERS', 2))

    print(f"🚀Запускаем {num_fast_workers} воркеров для быстрой очереди (YT/Saavn/SoundCloud)...")
    for i in range(num_fast_workers):
        asyncio.create_task(limitations.download_worker(
            bot_instance, queue=limitations.fast_queue, worker_id=f'Fast-{i+1}'
        ))

    print(f"🚀Запускаем {num_slow_workers} воркеров для медленной очереди (Yandex)...")
    for i in range(num_slow_workers):
        asyncio.create_task(limitations.download_worker(
            bot_instance, queue=limitations.slow_queue, worker_id=f'Slow-{i+1}'
        ))

    asyncio.create_task(start_periodic_db_cleanup())

async def on_shutdown(bot_instance: Bot):
    print("🔄Завершение работы бота...")
    try:
        from download_functions.yandex_music_api import cleanup_client
        await cleanup_client()
    except ImportError:
        pass
    print("✅Бот корректно завершен.")

if __name__ == '__main__':
    dp.include_router(info.router)
    dp.include_router(support.router)
    dp.include_router(main_router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    print("🚀 Поехали...")
    try:
        asyncio.run(dp.start_polling(bot, bot_instance=bot))
    except KeyboardInterrupt:
        print("\n🛑Получен сигнал завершения работы...")
    except Exception as e:
        print(f"❌Критическая ошибка: {e}")
    finally:
        print("🔚Работа завершена.")