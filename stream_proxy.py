# stream_proxy.py

import base64
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

app = FastAPI()

# Словарь MIME-типов, как у вас, очень полезен
MIME_TYPES = {
    'mp3': 'audio/mpeg',
    'm4a': 'audio/mp4',
    'ogg': 'audio/ogg',
    'opus': 'audio/opus',
    'flac': 'audio/flac',
    'wav': 'audio/wav',
    'webm': 'audio/webm',
}

async def stream_from_yt_dlp(track_url: str):
    """
    Асинхронный генератор, который запускает yt-dlp как подпроцесс
    и отдает (yield) его stdout по частям (чанками).
    """
    # Команда для запуска yt-dlp:
    # -f bestaudio/best: выбрать лучшее качество аудио
    # -o -: выводить результат в stdout, а не в файл
    # --quiet: не выводить лишнюю информацию в stderr
    args = [
        'yt-dlp',
        '--quiet',
        '-f', 'bestaudio/best',
        '-o', '-',
        track_url
    ]

    # Создаем асинхронный подпроцесс
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE  # Перехватываем ошибки тоже
    )

    # Асинхронно читаем stdout, пока процесс не завершится
    while True:
        try:
            # Читаем чанк данных из stdout
            chunk = await process.stdout.read(1024 * 64) # 64 KB
            if not chunk:
                # Если чанк пустой, значит поток закончился
                break
            yield chunk
        except asyncio.CancelledError:
            # Если клиент (Telegram) разорвал соединение, останавливаем процесс
            print(f"Client disconnected, terminating process for {track_url}")
            process.terminate()
            await process.wait()
            raise
        except Exception as e:
            print(f"Error while streaming from yt-dlp: {e}")
            process.kill()
            await process.wait()
            break # Прерываем цикл в случае ошибки

    # Ждем завершения процесса и проверяем код возврата
    return_code = await process.wait()
    if return_code != 0:
        # Если yt-dlp завершился с ошибкой, логируем ее
        error_output = await process.stderr.read()
        print(f"yt-dlp failed for {track_url} with code {return_code}: {error_output.decode()}")


@app.get("/stream/{encoded_payload}")
async def proxy_stream_hls(encoded_payload: str):
    """
    Основной эндпоинт, который принимает закодированный URL страницы трека
    и его расширение, а затем стримит аудиопоток от yt-dlp.
    """
    try:
        decoded_payload = base64.urlsafe_b64decode(encoded_payload).decode('utf-8')
        parts = decoded_payload.split('|', 1) # Разделяем только один раз
        if len(parts) != 2:
            raise ValueError("Invalid payload format. Expected 'webpage_url|extension'.")
        
        track_url, extension = parts
        print(f"Proxying HLS stream for: {track_url} (Format: {extension})")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid or malformed payload: {e}")

    # Определяем MIME-тип на основе расширения, полученного от бота
    media_type = MIME_TYPES.get(extension.lower(), 'application/octet-stream')

    # Возвращаем StreamingResponse, который будет вызывать наш асинхронный генератор
    return StreamingResponse(
        stream_from_yt_dlp(track_url),
        media_type=media_type
    )


@app.get("/")
def read_root():
    return {"status": "ok", "message": "HLS Audio Stream Proxy is running"}
