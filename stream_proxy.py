# stream_proxy.py

import base64
import asyncio
import yt_dlp
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from cachetools import TTLCache
import aiohttp

app = FastAPI()

# Кэш для хранения списков URL-ов сегментов.
# maxsize=1000: кэшируем до 1000 треков
# ttl=3600: храним кэш 1 час (ссылки на сегменты SoundCloud обычно живут несколько часов)
SEGMENTS_CACHE = TTLCache(maxsize=1000, ttl=3600)

# Единая сессия для aiohttp для переиспользования соединений
AIOHTTP_SESSION = None

@app.on_event("startup")
async def startup_event():
    global AIOHTTP_SESSION
    # Устанавливаем большой таймаут, т.к. скачивание может быть долгим
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=300)
    AIOHTTP_SESSION = aiohttp.ClientSession(timeout=timeout)

@app.on_event("shutdown")
async def shutdown_event():
    await AIOHTTP_SESSION.close()

MIME_TYPES = {
    'm4a': 'audio/mp4',
    'mp3': 'audio/mpeg',
    'opus': 'audio/opus',
}

async def get_hls_segment_urls(track_url: str) -> list:
    """
    Запускает yt-dlp один раз, чтобы получить JSON с информацией о формате,
    включая список URL всех сегментов HLS-потока.
    """
    if track_url in SEGMENTS_CACHE:
        print(f"CACHE HIT for {track_url}")
        return SEGMENTS_CACHE[track_url]

    print(f"CACHE MISS. Fetching segments for {track_url}")
    ydl_opts = {
        'quiet': True,
        'dump_single_json': True, # Не скачивать, а выдать JSON с информацией
        'format': 'bestaudio[ext=m4a]/bestaudio/best', # Приоритет m4a
    }
    
    # Используем run_in_executor, т.к. yt-dlp - блокирующая операция
    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info_dict = await loop.run_in_executor(
                None, lambda: ydl.extract_info(track_url, download=False)
            )
        except Exception as e:
            print(f"yt-dlp extract_info failed: {e}")
            raise HTTPException(status_code=502, detail="Upstream service (yt-dlp) failed.")

    # Находим URL-ы сегментов внутри JSON
    fragments = info_dict.get('fragments')
    if not fragments:
        # Если это не HLS, а прямая ссылка (редко, но бывает), вернем ее
        direct_url = info_dict.get('url')
        if direct_url:
             segment_urls = [direct_url]
             SEGMENTS_CACHE[track_url] = segment_urls
             return segment_urls
        raise HTTPException(status_code=404, detail="Could not find audio fragments or direct URL.")
    
    segment_urls = [f['url'] for f in fragments]
    
    # Кэшируем результат
    SEGMENTS_CACHE[track_url] = segment_urls
    return segment_urls


async def stream_direct_segments(segment_urls: list):
    """
    Асинхронно скачивает сегменты по списку URL-ов и отдает их байты.
    Это сверхбыстрая операция по сравнению с запуском yt-dlp.
    """
    try:
        for url in segment_urls:
            async with AIOHTTP_SESSION.get(url) as response:
                response.raise_for_status()
                async for chunk in response.content.iter_chunked(1024 * 64):
                    yield chunk
    except aiohttp.ClientError as e:
        print(f"AIOHTTP Error while streaming segment: {e}")
        # Не бросаем HTTPException, просто прекращаем стрим
    except asyncio.CancelledError:
        print("Client disconnected, stream cancelled.")
        raise
    except Exception as e:
        print(f"Unexpected error in stream_direct_segments: {e}")


@app.get("/stream/{encoded_payload}")
async def proxy_stream_hls(encoded_payload: str):
    try:
        decoded_payload = base64.urlsafe_b64decode(encoded_payload).decode('utf-8')
        parts = decoded_payload.split('|', 1)
        if len(parts) != 2:
            raise ValueError("Invalid payload format.")
        
        track_url, extension = parts
        print(f"Request for: {track_url} (Format: {extension})")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    # 1. Получаем список URL-ов сегментов (из кэша или через yt-dlp)
    segment_urls = await get_hls_segment_urls(track_url)

    # 2. Стримим эти сегменты напрямую
    media_type = MIME_TYPES.get(extension.lower(), 'application/octet-stream')
    return StreamingResponse(
        stream_direct_segments(segment_urls),
        media_type=media_type
    )

@app.get("/")
def read_root():
    return {"status": "ok", "message": "High-Speed HLS Audio Proxy is running"}
