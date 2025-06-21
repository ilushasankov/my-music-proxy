# soundcloud_api.py
import asyncio
import yt_dlp
import traceback
import aiohttp

async def get_soundcloud_info(track_url: str) -> dict | None:
    """
    Извлекает прямую ссылку на аудиопоток и все необходимые метаданные,
    не скачивая сам файл.
    """
    if not track_url or not track_url.startswith('http'):
        print(f"SoundCloud info error: Invalid URL received: '{track_url}'")
        return None

    loop = asyncio.get_event_loop()
    # Опции для получения информации
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestaudio/best', # Важно, чтобы yt-dlp выбрал лучший аудиоформат
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Получаем полную информацию, не скачивая
            info_dict = await loop.run_in_executor(
                None, lambda: ydl.extract_info(track_url, download=False)
            )

        if not info_dict:
            return None

        # yt-dlp отдает прямую ссылку в поле 'url' после извлечения информации
        direct_url = info_dict.get('url')
        if not direct_url:
            print(f"Could not find direct stream URL for {track_url}")
            return None

        artist = info_dict.get('uploader') or "Unknown Artist"
        title = info_dict.get('title') or "Unknown Title"
        if title.lower().startswith(artist.lower() + ' - '):
            title = title[len(artist) + 3:]

        # Собираем все в один словарь
        return {
            'webpage_url': info_dict.get('webpage_url', track_url),
            'artist': artist,
            'title': title,
            'duration': info_dict.get('duration'),
            'thumbnail_url': info_dict.get('thumbnail'),
            'ext': info_dict.get('ext', 'mp3'), # Очень важное поле!
        }

    except Exception as e:
        print(f"Failed to get SoundCloud stream info for {track_url}: {e}")
        traceback.print_exc()
        return None

# Функции поиска остаются без изменений, просто убедитесь, что они есть
# (я скопировал вашу функцию поиска из исходного кода для полноты)
async def search_tracks_soundcloud(query: str, limit: int = 10) -> list:
    """Ищет треки на SoundCloud с помощью yt-dlp."""
    loop = asyncio.get_event_loop()
    YDL_OPTS_INFO = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'default_search': 'scsearch',
    }
    with yt_dlp.YoutubeDL(YDL_OPTS_INFO) as ydl:
        try:
            search_query = f"scsearch{limit}:{query}"
            search_result = await loop.run_in_executor(
                None, lambda: ydl.extract_info(search_query, download=False)
            )
            tracks = []
            if 'entries' in search_result:
                for entry in search_result['entries']:
                    if entry and entry.get('duration'):
                        artist = entry.get('uploader') or "Unknown Artist"
                        title = entry.get('title') or "Unknown Title"
                        if title.lower().startswith(artist.lower() + ' - '):
                            title = title[len(artist) + 3:]
                        tracks.append({
                            'id': entry['id'], 
                            'url': entry.get('webpage_url') or entry.get('url'),
                            'source': 'soundcloud',
                            'title': title,
                            'artist': artist,
                            'duration': entry.get('duration'),
                            'thumbnail_url': entry.get('thumbnail'),
                        })
            return tracks
        except Exception as e:
            print(f"SoundCloud search error: {e}")
            traceback.print_exc()
            return []
