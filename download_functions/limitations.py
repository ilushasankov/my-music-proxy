import asyncio
import time
from typing import Tuple, Optional
import traceback
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import BufferedInputFile, URLInputFile
from cachetools import TTLCache

from download_functions.database import get_user_daily_downloads, save_user_track, cleanup_expired_cache
from download_functions.yt_download import download_track_optimized, sanitize_filename
from download_functions.saavn_api import download_track_saavn
from download_functions.yandex_music_api import download_track_yandex

import base64
from download_functions.soundcloud_api import get_soundcloud_info 

import os

# --- КОНФИГУРАЦИЯ ---
CHANNEL_ID_1 = os.getenv('CHANNEL_ID_1')
CHANNEL_ID_2 = os.getenv('CHANNEL_ID_2')

# --- ОПТИМИЗИРОВАННЫЕ ЛИМИТЫ ---
BASE_DOWNLOAD_LIMIT = 5
PREMIUM_DOWNLOAD_LIMIT = 42
SEARCH_COOLDOWN = 8
DOWNLOAD_STATUS_UPDATE_INTERVAL = 12

# --- ЛИМИТЫ КОНТЕНТА ---
MAX_DURATION_SECONDS = 900  # 15 минут максимум

FAST_QUEUE_MAX_SIZE = 200
SLOW_QUEUE_MAX_SIZE = 50

# Приоритеты: 0 - самый высокий
PRIORITY_PREMIUM = 0
PRIORITY_NORMAL = 1

# Очереди для разных типов задач. Используем PriorityQueue.
# Элемент в очереди будет кортежем: (priority, timestamp, (call, source, track_id))
fast_queue = asyncio.PriorityQueue(maxsize=FAST_QUEUE_MAX_SIZE)
slow_queue = asyncio.PriorityQueue(maxsize=SLOW_QUEUE_MAX_SIZE)

ESTIMATED_TIME_PER_SOURCE = {
    'fast': 15,  # секунд на обработку одного трека в быстрой очереди
    'slow': 45,  # секунд на обработку одного трека в медленной очереди
}

# --- СОСТОЯНИЯ В ПАМЯТИ ---
downloading_users = set()
user_last_search = TTLCache(maxsize=10000, ttl=SEARCH_COOLDOWN)
subscription_cache = TTLCache(maxsize=5000, ttl=300)


async def check_subscription(bot: Bot, user_id: int, channel_id: str) -> bool:
    if not channel_id: return True
    cache_key = f"{user_id}:{channel_id}"
    if cache_key in subscription_cache: return subscription_cache[cache_key]
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        is_subscribed = member.status in ['member', 'administrator', 'creator']
        subscription_cache[cache_key] = is_subscribed
        return is_subscribed
    except TelegramBadRequest:
        subscription_cache[cache_key] = False
        return False
    except Exception:
        return False

# <<< ИЗМЕНЕНИЕ: Логика проверки лимитов переработана для поддержки новых очередей >>>
async def check_all_limits(bot: Bot, user_id: int, source: str) -> Tuple[bool, str, int, bool]:
    """
    Проверяет все лимиты и возвращает:
    (возможность скачивания, сообщение для пользователя, позиция в очереди, является ли пользователь премиум)
    """
    main_sub_task = asyncio.create_task(check_subscription(bot, user_id, CHANNEL_ID_1))
    premium_sub_task = asyncio.create_task(check_subscription(bot, user_id, CHANNEL_ID_2))
    downloads_task = asyncio.create_task(get_user_daily_downloads(user_id))
    
    is_subscribed_main = await main_sub_task
    
    if not is_subscribed_main:
        message = (f"🤨Для использования бота необходимо подписаться на канал: {CHANNEL_ID_1}\n\n"
                   "После подписки нажми на кнопку скачивания еще раз🔄")
        return False, message, 0, False

    if user_id in downloading_users:
        return False, "👉👈Пожалуйста, подожди, пока завершится предыдущая загрузка...", 0, False

    is_subscribed_premium = await premium_sub_task
    downloads_today = await downloads_task
    
    current_limit = PREMIUM_DOWNLOAD_LIMIT if is_subscribed_premium else BASE_DOWNLOAD_LIMIT
    
    if downloads_today >= current_limit:
        message = (f"💀Ты достиг дневного лимита в {current_limit} треков. Лимит обновляется раз в 24 часа...")
        if not is_subscribed_premium and CHANNEL_ID_2:
            message += f"\n\n✨Чтобы увеличить лимит до {PREMIUM_DOWNLOAD_LIMIT} треков, подпишись на: {CHANNEL_ID_2}"
        return False, message, 0, is_subscribed_premium

    # Определяем, в какую очередь пойдет задача
    target_queue = slow_queue if source == 'yandex' else fast_queue
    queue_type = 'slow' if source == 'yandex' else 'fast'
    
    # Проверяем, не переполнена ли очередь
    if target_queue.full():
        return False, "😥Сервер сейчас перегружен. Пожалуйста, попробуй скачать трек через минуту.", 0, is_subscribed_premium

    queue_position = target_queue.qsize() + 1
    
    # Собираем информативное сообщение
    est_wait_seconds = queue_position * ESTIMATED_TIME_PER_SOURCE[queue_type]
    est_wait_min = int(est_wait_seconds // 60)
    
    wait_message = ""
    if est_wait_min > 0:
        wait_message = f"Примерное время ожидания: ~{est_wait_min} мин."

    premium_status_msg = " (VIP-приоритет ✨)" if is_subscribed_premium else ""
    
    message = (f"✅Ты добавлен в очередь{premium_status_msg}.\n"
               f"Позиция: {queue_position}. {wait_message}")
               
    return True, message, queue_position, is_subscribed_premium

def check_search_rate_limit(user_id: int) -> Optional[str]:
    if user_id in user_last_search:
        remaining_time = SEARCH_COOLDOWN - (time.time() - user_last_search[user_id])
        return f"Ты слишком часто отправляешь запросы😤. Подожди {int(remaining_time)} сек."
    user_last_search[user_id] = time.time()
    return None

def is_duration_valid(duration: int) -> bool:
    return 0 < duration <= MAX_DURATION_SECONDS

async def download_worker(bot: Bot, queue: asyncio.PriorityQueue, worker_id: str):
    print(f"🔧Воркер скачиваний #{worker_id} запущен...")
    
    PROXY_URL = os.getenv('PROXY_URL')
    if not PROXY_URL:
        print("CRITICAL ERROR: PROXY_URL is not set in .env file! Bot will not work.")
        return

    while True:
        try:
            _priority, _timestamp, (call, source, track_id) = await queue.get()
            user_id = call.from_user.id
            print(f"[Worker {worker_id}] Processing {track_id} ({source}) for {user_id}")

            await call.message.edit_text("🚀 Готовлю ссылку...")

            info = None
            audio_source = None 
            full_title = ""
            file_name = "audio.mp3"

            # <<< ИЗМЕНЕНИЕ ЛОГИКИ ДЛЯ SOUNDCLOUD >>>
            if source == 'soundcloud':
                # track_id для soundcloud - это и есть URL страницы
                info = await get_soundcloud_info(track_id) 
                if info:
                    full_title = f"{info.get('artist', 'Unknown Artist')} - {info.get('title', 'Unknown Title')}"
                    
                    # Получаем URL страницы и расширение файла
                    webpage_url = info['webpage_url']
                    extension = info.get('ext', 'mp3')
                    
                    # Создаем payload: "URL_страницы|расширение"
                    payload_to_encode = f"{webpage_url}|{extension}"
                    
                    # Кодируем payload в base64 для безопасной передачи в URL
                    encoded_payload = base64.urlsafe_b64encode(payload_to_encode.encode()).decode()
                    
                    # Формируем ссылку на НАШ прокси-сервер
                    proxy_link = f"{PROXY_URL.rstrip('/')}/stream/{encoded_payload}"
                    
                    # Готовим имя файла. Это КРИТИЧЕСКИ важно для Telegram.
                    file_name = f"{sanitize_filename(full_title)}.{extension}"
                    
                    # Создаем URLInputFile с proxy_link и ПРАВИЛЬНЫМ именем файла
                    audio_source = URLInputFile(proxy_link, filename=file_name)
                    print(f"[Worker {worker_id}] Generated proxy link for SoundCloud: {file_name}")
                else:
                    await call.message.edit_text("❌ Не удалось получить информацию о треке с SoundCloud.")
                    continue # Переходим к следующей задаче в очереди

            # Логика для других источников (yandex, saavn, yt) остается без изменений
            else:
                if source == 'yandex': info = await download_track_yandex(track_id)
                elif source == 'saavn': info = await download_track_saavn(track_id)
                else: info = await download_track_optimized(track_id)

                if not info:
                    await call.message.edit_text("❌ Не удалось скачать трек. Возможно, он недоступен.")
                    continue

                if 'audio_bytes' in info:
                    full_title = f"{info.get('artist', 'Unknown Artist')} - {info.get('title', 'Unknown Title')}"
                    file_extension = info.get('extension', 'm4a')
                    file_name = f"{sanitize_filename(full_title)}.{file_extension}"
                    audio_source = BufferedInputFile(info['audio_bytes'], filename=file_name)

            if not audio_source:
                await call.message.edit_text("❌ Внутренняя ошибка: источник аудио не определен.")
                continue

            # ... (остальная часть функции: подготовка метаданных, отправка)
            
            title = info.get('title', 'Unknown Title')
            artist = info.get('artist', 'Unknown Artist')
            duration = info.get('duration')
            
            thumbnail = None
            if info.get('thumbnail_url'):
                thumbnail = URLInputFile(info.get('thumbnail_url'))
            elif info.get('thumbnail_bytes'):
                thumbnail = BufferedInputFile(info.get('thumbnail_bytes'), 'thumb.jpg')

            source_icons = {'yandex': '💛', 'saavn': '💛', 'soundcloud': '☁️', 'yt': '📮'}
            # Используем full_title, который мы определили ранее
            caption = f"{source_icons.get(source, '🎧')} `{full_title}`"
            
            await call.message.edit_text("✅ Отправляю...")
            
            await bot.send_audio(
                chat_id=user_id,
                audio=audio_source, # audio_source уже содержит filename
                caption=caption,
                parse_mode="Markdown",
                title=title,
                performer=artist,
                duration=int(duration) if duration else None,
                thumbnail=thumbnail
            )
            await call.message.delete()
            await save_user_track(user_id, f"{source}_{track_id}", info)
            print(f"[Worker {worker_id}] Success: Sent {full_title} from {source}")

        except Exception as e:
            print(f"[Worker {worker_id}] Critical error in worker: {e}")
            traceback.print_exc()
            if 'call' in locals():
                try:
                    await call.message.edit_text("❌ Произошла непредвиденная ошибка при обработке вашего запроса.")
                except:
                    pass
        finally:
            if 'user_id' in locals() and user_id in downloading_users:
                downloading_users.remove(user_id)
            queue.task_done()

async def start_periodic_db_cleanup():
    while True:
        await asyncio.sleep(3600)
        await cleanup_expired_cache()
