# stream_proxy.py
import base64
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import aiohttp

app = FastAPI()

# Устанавливаем таймаут для сессии, чтобы избежать вечных зависаний
CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=300)

async def stream_audio_from_url(url: str):
    """
    Асинхронный генератор, который скачивает аудио по URL
    и отдает его по частям (чанками).
    """
    try:
        async with aiohttp.ClientSession(timeout=CLIENT_TIMEOUT) as session:
            # Важно: yt-dlp часто выдает ссылки, которые требуют стандартный User-Agent
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()  # Вызовет исключение для кодов 4xx/5xx
                
                # Стримим ответ по частям
                async for chunk in response.content.iter_chunked(1024 * 64): # чанки по 64 KB
                    yield chunk
                    await asyncio.sleep(0.001) # Небольшая пауза, чтобы не блокировать event loop

    except aiohttp.ClientError as e:
        print(f"Proxy Error: aiohttp client error: {e}")
        # Нельзя здесь ничего возвращать, иначе FastAPI упадет. Ошибки должны обрабатываться на уровне роута.
        raise HTTPException(status_code=502, detail=f"Failed to fetch content from upstream: {e}")
    except Exception as e:
        print(f"Proxy Error: Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {e}")

@app.get("/stream/{encoded_url}")
async def proxy_stream(encoded_url: str):
    """
    Основной эндпоинт прокси.
    Принимает URL, закодированный в Base64.
    """
    try:
        # Декодируем URL из безопасного для URL формата Base64
        decoded_url = base64.urlsafe_b64decode(encoded_url).decode('utf-8')
        print(f"Proxying request for: {decoded_url[:80]}...")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Base64 URL: {e}")

    # Используем StreamingResponse для передачи потока "на лету"
    return StreamingResponse(
        stream_audio_from_url(decoded_url),
        media_type="audio/mpeg" # Используем общий MIME-тип
    )

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Audio stream proxy is running"}

# Для локального запуска: uvicorn stream_proxy:app --reload --port 8000