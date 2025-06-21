import base64
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import aiohttp

app = FastAPI()

CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=300)

MIME_TYPES = {
    'mp3': 'audio/mpeg',
    'm4a': 'audio/mp4',
    'ogg': 'audio/ogg',
    'opus': 'audio/opus',
    'flac': 'audio/flac',
    'wav': 'audio/wav',
}

async def stream_audio_from_url(url: str):
    try:
        async with aiohttp.ClientSession(timeout=CLIENT_TIMEOUT) as session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                async for chunk in response.content.iter_chunked(1024 * 64):
                    yield chunk
                    await asyncio.sleep(0.001)
    except aiohttp.ClientError as e:
        print(f"Proxy Error: aiohttp client error: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch content from upstream: {e}")
    except Exception as e:
        print(f"Proxy Error: Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {e}")

@app.get("/stream/{encoded_payload}")
async def proxy_stream(encoded_payload: str):
    """
    Основной эндпоинт прокси.
    Принимает payload (URL|расширение), закодированный в Base64.
    """
    try:
        decoded_payload = base64.urlsafe_b64decode(encoded_payload).decode('utf-8')
        # Разделяем строку на URL и расширение
        parts = decoded_payload.split('|')
        if len(parts) != 2:
            raise ValueError("Invalid payload format. Expected 'url|extension'.")
        
        audio_url, extension = parts
        print(f"Proxying request for: {audio_url[:80]}... (Format: {extension})")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    # Определяем правильный MIME-тип, с фолбэком на 'application/octet-stream'
    media_type = MIME_TYPES.get(extension, 'application/octet-stream')

    return StreamingResponse(
        stream_audio_from_url(audio_url),
        media_type=media_type # Используем определенный MIME-тип
    )

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Audio stream proxy is running"}
