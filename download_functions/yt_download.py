import yt_dlp
import os
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import tempfile
import hashlib
from mutagen.mp4 import MP4, MP4Cover
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError, TIT2, TPE1, APIC
from dotenv import load_dotenv

load_dotenv()

SEMAPHORE_LIMIT = int(os.getenv('YOUTUBE_SEMAPHORE_LIMIT', 4))
YOUTUBE_SEMAPHORE = asyncio.Semaphore(SEMAPHORE_LIMIT)

# Пул потоков для параллельной обработки I/O-bound задач
executor = ThreadPoolExecutor(max_workers=(os.cpu_count() or 1) * 2)

def sanitize_filename(filename: str) -> str:
    """Удаляет символы, недопустимые в именах файлов."""
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

def get_optimized_ydl_opts(output_path: str = None):
    """Возвращает оптимизированные опции для yt-dlp."""
    opts = {
        # Приоритет отдается формату 140 (стандартный M4A 128kbps), он почти всегда доступен и быстро качается.
        'format': '140/bestaudio[ext=m4a]/bestaudio',
        'quiet': True,
        'no_warnings': True,
        'logtostderr': False,
        'noplaylist': True,
        'extract_flat': False,
        
        # Увеличиваем таймаут сокета до 60 секунд. Этого должно хватить в большинстве случаев.
        'socket_timeout': 60,
        
        # Увеличиваем количество попыток при ошибках сети.
        'retries': 4,
        'fragment_retries': 4,
        
        'skip_unavailable_fragments': True,
        'keep_fragments': False,
        'writeinfojson': False,
        'writethumbnail': False,
        'noprogress': True,
    }
    
    if output_path:
        opts['outtmpl'] = output_path
    else:
        opts['outtmpl'] = '-'  # Для скачивания в stdout
    
    return opts

def get_search_ydl_opts():
    """Оптимизированные опции для поиска."""
    return {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': True,
        'default_search': 'ytsearch',
        'socket_timeout': 15,
        'extractor_retries': 1,
        'playlist_items': '1-30',
    }

def clean_title_advanced(raw_title: str, uploader: str = None) -> tuple[str, str]:
    """Улучшенная очистка названий треков."""
    junk_patterns = [
        r'\b(official|audio|video|lyric|lyrics|visualizer|hq|hd|4k|mv|1080p|720p|music|vevo)\b',
        r'\[.*?(official|audio|video|lyric|lyrics|visualizer|hq|hd|4k|mv|1080p|720p|music|vevo).*?\]',
        r'\(.*?(official|audio|video|lyric|lyrics|visualizer|hq|hd|4k|mv|1080p|720p|music|vevo).*?\)',
    ]
    
    clean_title = raw_title
    for pattern in junk_patterns:
        clean_title = re.sub(pattern, '', clean_title, flags=re.IGNORECASE)
    
    clean_title = re.sub(r'\[\s*\]|\(\s*\)', '', clean_title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    clean_title = re.sub(r'^[-\s]+|[-\s]+$', '', clean_title)
    
    artist, title = None, clean_title
    
    separators = [' - ', ' – ', ' — ', ' | ', ': ']
    for sep in separators:
        if sep in clean_title:
            parts = clean_title.split(sep, 1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                potential_artist, potential_title = parts[0].strip(), parts[1].strip()
                if not re.match(r'^(track|song|audio)\s*\d*$', potential_artist.lower()):
                    artist, title = potential_artist, potential_title
                    break
    
    if not artist and uploader:
        artist = uploader
        suffixes = [" - Topic", "VEVO", " - Official", " Official", "Records", "Music"]
        for suffix in suffixes:
            if artist.endswith(suffix):
                artist = artist.replace(suffix, "").strip()
    
    return artist or "Unknown Artist", title or "Unknown Title"


async def search_tracks_optimized(query: str, limit: int = 30) -> list[dict]:
    """Оптимизированный поиск треков с ограничением через семафор."""
    def _search():
        with yt_dlp.YoutubeDL(get_search_ydl_opts()) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                results = []
                for entry in info.get('entries', []):
                    if entry and 'id' in entry:
                        duration = entry.get('duration', 0) or 0
                        if 0 < duration <= 900: # Ограничение длительности 15 минут
                            results.append({
                                'id': entry['id'],
                                'title': entry.get('title', 'Unknown Title'),
                                'duration': duration
                            })
                return results[:limit]
            except Exception as e:
                print(f"Search error in thread: {e}")
                return []

    # Оборачиваем вызов в семафор
    async with YOUTUBE_SEMAPHORE:
        try:
            results = await asyncio.get_event_loop().run_in_executor(executor, _search)
        except Exception as e:
            print(f"Semaphore/Executor error during search: {e}")
            results = []
    return results

async def download_track_optimized(video_id: str) -> dict | None:
    """Оптимизированное скачивание трека с ограничением через семафор."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    def _download():
        with tempfile.NamedTemporaryFile(delete=False, suffix='.%(ext)s') as temp_file:
            temp_path = temp_file.name
        
        output_template = temp_path.replace('.%(ext)s', '.%(ext)s')
        ydl_opts = get_optimized_ydl_opts(output_template)
        
        info = None
        downloaded_file_path = None
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                pre_info = ydl.extract_info(url, download=False)
                duration = pre_info.get('duration', 0) or 0
                if duration > 900:
                    return None
                
                info = ydl.extract_info(url, download=True)
                file_extension = info.get('ext', 'm4a')
                downloaded_file_path = temp_path.replace('.%(ext)s', f'.{file_extension}')

        except Exception as e:
            print(f"Download process error: {e}")
            if downloaded_file_path and os.path.exists(downloaded_file_path): os.unlink(downloaded_file_path)
            return None

        if not info or not os.path.exists(downloaded_file_path):
            return None

        try:
            with open(downloaded_file_path, 'rb') as f:
                audio_bytes = f.read()
            
            if not audio_bytes: return None
            
            artist, title = clean_title_advanced(info.get('title', 'Unknown Title'), info.get('uploader'))
            audio_file_in_memory = io.BytesIO(audio_bytes)
            file_extension = info.get('ext', 'm4a')

            try:
                if file_extension == 'm4a':
                    audio = MP4(audio_file_in_memory); audio['\xa9nam'] = [title]; audio['\xa9ART'] = [artist]; audio.save(audio_file_in_memory)
                elif file_extension == 'mp3':
                    try: audio = EasyID3(audio_file_in_memory)
                    except ID3NoHeaderError: audio = EasyID3()
                    audio['title'] = title; audio['artist'] = artist; audio.save(audio_file_in_memory)
            except Exception as e:
                print(f"Could not write metadata for {video_id}: {e}")
            
            final_audio_bytes = audio_file_in_memory.getvalue()
            audio_file_in_memory.close()
            
            return {'audio_bytes': final_audio_bytes, 'title': title, 'artist': artist, 'duration': info.get('duration', 0), 'extension': file_extension}
        except Exception as e:
            print(f"Error processing file in thread: {e}")
            return None
        finally:
            if downloaded_file_path and os.path.exists(downloaded_file_path): os.unlink(downloaded_file_path)

    # Оборачиваем вызов в семафор
    async with YOUTUBE_SEMAPHORE:
        try:
            result = await asyncio.get_event_loop().run_in_executor(executor, _download)
        except Exception as e:
            print(f"Semaphore/Executor error during download: {e}")
            result = None
    return result

# Алиасы для обратной совместимости
search_tracks = search_tracks_optimized
download_track = download_track_optimized