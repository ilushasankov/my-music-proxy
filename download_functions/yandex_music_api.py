import os
import asyncio
import traceback
from yandex_music import ClientAsync
from dotenv import load_dotenv
import aiohttp 

load_dotenv()

YANDEX_TOKEN = os.getenv('YANDEX_MUSIC_TOKEN')
client: ClientAsync = None

yandex_api_semaphore = asyncio.Semaphore(2)


async def init_yandex_music_client():
    """Инициализирует асинхронный клиент Яндекс.Музыки. (ВАШ ОРИГИНАЛЬНЫЙ, РАБОЧИЙ КОД)"""
    global client
    if not YANDEX_TOKEN:
        print("⚠️ Токен Яндекс.Музыки (YANDEX_MUSIC_TOKEN) не найден в .env. Функционал Яндекса будет отключен.")
        return
    try:
        client = ClientAsync(YANDEX_TOKEN)
        await client.init()
        
        account_status = await client.account_status()
        if not account_status:
            raise ValueError("Не удалось авторизоваться с предоставленным токеном.")
        
        print("✅ Клиент Яндекс.Музыки успешно инициализирован.")
    except Exception as e:
        print(f"❌ Ошибка инициализации клиента Яндекс.Музыки: {e}")
        client = None


async def search_tracks_yandex(query: str, limit: int = 15) -> list[dict]:
    """Ищет треки через API Яндекс.Музыки. (ВАШ ОРИГИНАЛЬНЫЙ КОД + СЕМАФОР)"""
    if not client:
        return []

    # Оборачиваем вызов в семафор
    async with yandex_api_semaphore:
        try:
            # Ваша логика поиска
            search_result = await client.search(query, type_='track', page=0, nocorrect=False)
            if not search_result or not search_result.tracks:
                return []

            results = []
            for track in search_result.tracks.results[:limit]:
                if not track.available or track.duration_ms == 0 or track.duration_ms > 900000:
                    continue
                
                artist_names = ', '.join([artist.name for artist in track.artists])
                
                results.append({
                    'id': f"{track.id}:{track.albums[0].id if track.albums else ''}",
                    'title': track.title,
                    'artist': artist_names,
                    'duration': track.duration_ms // 1000,
                    'source': 'yandex',
                    'thumbnail_url': f"https://{track.cover_uri.replace('%%', '200x200')}" if track.cover_uri else None
                })
            return results
        except Exception as e:
            print(f"An error occurred during Yandex.Music search: {e}")
            return []

async def download_with_retry(track, max_retries=3, initial_delay=1):
    """(ВАША ОРИГИНАЛЬНАЯ ФУНКЦИЯ, БЕЗ ИЗМЕНЕНИЙ)"""
    for attempt in range(max_retries):
        try:
            print(f"Попытка скачивания #{attempt + 1}/{max_retries}")
            download_info = await track.get_download_info_async()
            if not download_info:
                print("Нет доступной информации для скачивания")
                continue
            
            best_quality = None
            for info in download_info:
                if info.codec == 'mp3':
                    if not best_quality or info.bitrate_in_kbps > best_quality.bitrate_in_kbps:
                        best_quality = info
            
            if not best_quality:
                best_quality = download_info[0]
            
            audio_bytes = await asyncio.wait_for(best_quality.download_bytes_async(), timeout=180.0)
            
            if audio_bytes and len(audio_bytes) > 1000:
                return audio_bytes, best_quality.codec
            else:
                print(f"Получен пустой или слишком маленький файл: {len(audio_bytes) if audio_bytes else 0} байт")
                
        except asyncio.TimeoutError:
            print(f"Таймаут на попытке #{attempt + 1}")
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                print(f"Ожидание {delay} секунд перед следующей попыткой...")
                await asyncio.sleep(delay)
            continue
        except Exception as e:
            print(f"Ошибка на попытке #{attempt + 1}: {e}")
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            continue
    
    return None, None


async def download_track_yandex(track_album_id: str) -> dict | None:
    """Скачивает трек по ID из Яндекс.Музыки. (ВАШ ОРИГИНАЛЬНЫЙ КОД + СЕМАФОР)"""
    if not client:
        print("Клиент Яндекс.Музыки не инициализирован")
        return None
    
    async with yandex_api_semaphore:
        try:
            track_id = track_album_id.split(':')[0]
            print(f"Скачиваем трек с ID: {track_id}")
            
            full_track_objects = await asyncio.wait_for(client.tracks([track_id]), timeout=30.0)
            
            if not full_track_objects:
                print("Трек не найден")
                return None
            
            track = full_track_objects[0]
            
            if not track.available:
                print("Трек недоступен для скачивания")
                return None
            
            if track.duration_ms > 900000:
                print(f"Трек слишком длинный: {track.duration_ms // 1000} секунд")
                return None

            audio_bytes, codec = await download_with_retry(track)
            
            if not audio_bytes:
                print("Не удалось скачать аудио после всех попыток")
                return None
            
            print(f"Аудио успешно скачано: {len(audio_bytes)} байт")
            
            thumbnail_bytes = None
            try:
                thumbnail_bytes = await asyncio.wait_for(track.download_cover_bytes_async(size='200x200'), timeout=15.0)
                print("Обложка успешно скачана")
            except Exception as e:
                print(f"Warning: Could not download cover: {e}")

            artist_names = ', '.join([artist.name for artist in track.artists])
            extension = 'mp3' if codec == 'mp3' else 'm4a'

            result = {
                'audio_bytes': audio_bytes, 'title': track.title, 'artist': artist_names,
                'duration': track.duration_ms // 1000, 'extension': extension,
                'thumbnail_bytes': thumbnail_bytes,
            }
            
            print(f"Трек успешно подготовлен: {artist_names} - {track.title}")
            return result
            
        except asyncio.TimeoutError:
            print("Общий таймаут операции скачивания")
            return None
        except Exception as e:
            print(f"An error occurred during Yandex.Music download: {e}")
            traceback.print_exc()
            return None


async def cleanup_client():
    """Очищает ресурсы клиента при завершении работы. (ВАШ ОРИГИНАЛЬНЫЙ КОД)"""
    global client
    if client and hasattr(client, '_session') and client._session:
        try:
            await client._session.close()
            print("Сессия Яндекс.Музыки закрыта")
        except Exception as e:
            print(f"Ошибка при закрытии сессии: {e}")
    client = None