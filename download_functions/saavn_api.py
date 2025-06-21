import asyncio
import aiohttp
import io

API_BASE_URL = "https://saavn.dev/api" # Основной URL API

async def search_tracks_saavn(query: str, limit: int = 20) -> list[dict]:
    """
    Ищет треки через API Saavn.
    Возвращает список треков в стандартизированном формате.
    """
    search_url = f"{API_BASE_URL}/search/songs"
    params = {"query": query, "limit": limit}
    
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params, timeout=10) as response:
                if response.status != 200:
                    print(f"Saavn API search error: HTTP {response.status}")
                    return []
                
                data = await response.json()
                if not data.get('success') or not data['data'].get('results'):
                    return []
                
                for track in data['data']['results']:
                    # Пропускаем треки без URL для скачивания
                    if not track.get('downloadUrl'):
                        continue

                    # Формируем стандартизированный результат
                    results.append({
                        'id': track['id'],
                        'title': track['name'],
                        'artist': ', '.join([artist['name'] for artist in track.get('artists', {}).get('primary', [])]),
                        'duration': int(track.get('duration', 0)),
                        'source': 'saavn',  # Указываем источник
                        'thumbnail_url': track['image'][-1]['url'] # Берем самое высокое качество
                    })
    except Exception as e:
        print(f"An error occurred during Saavn search: {e}")
        return []
        
    return results

def _get_best_download_link(download_urls: list) -> str | None:
    """Выбирает наилучшую ссылку для скачивания (предпочтительно 320kbps)."""
    quality_order = {'320kbps': 1, '160kbps': 2, '96kbps': 3}
    best_link = None
    min_order = float('inf')

    for item in download_urls:
        quality = item.get('quality')
        link = item.get('url')
        if quality in quality_order and link:
            order = quality_order[quality]
            if order < min_order:
                min_order = order
                best_link = link
    return best_link


async def download_track_saavn(song_id: str) -> dict | None:
    """
    Скачивает трек по ID из Saavn API, включая аудио и обложку.
    """
    song_details_url = f"{API_BASE_URL}/songs/{song_id}"
    
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Получаем детали трека, включая ссылки на скачивание
            async with session.get(song_details_url, timeout=10) as response:
                if response.status != 200:
                    print(f"Saavn API song details error: HTTP {response.status}")
                    return None
                
                data = await response.json()
                if not data.get('success') or not data['data']:
                    return None
            
            song_data = data['data'][0]
            download_url = _get_best_download_link(song_data.get('downloadUrl', []))
            
            if not download_url:
                print(f"No download URL found for song {song_id}")
                return None

            # 2. Асинхронно скачиваем аудиофайл и обложку
            audio_task = asyncio.create_task(session.get(download_url, timeout=60))
            thumbnail_url = song_data['image'][-1]['url']
            thumbnail_task = asyncio.create_task(session.get(thumbnail_url, timeout=15))

            audio_response, thumbnail_response = await asyncio.gather(audio_task, thumbnail_task)

            # 3. Обрабатываем результаты
            if audio_response.status != 200:
                print(f"Failed to download audio from {download_url}")
                return None
            audio_bytes = await audio_response.read()

            thumbnail_bytes = None
            if thumbnail_response.status == 200:
                thumbnail_bytes = await thumbnail_response.read()

            return {
                'audio_bytes': audio_bytes,
                'title': song_data['name'],
                'artist': ', '.join([artist['name'] for artist in song_data.get('artists', {}).get('primary', [])]),
                'duration': int(song_data.get('duration', 0)),
                'extension': 'm4a', # Saavn обычно отдает m4a
                'thumbnail_bytes': thumbnail_bytes
            }
            
    except Exception as e:
        print(f"An error occurred during Saavn download: {e}")
        return None