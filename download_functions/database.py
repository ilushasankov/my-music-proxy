import aiosqlite
import json
import time
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "music_bot.db"
CACHE_TTL = 7200  # 2 часа в секундах для кэша поиска и ссылок

async def init_db():
    """Инициализирует базу данных и создает таблицы, если их нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_downloads (
                user_id INTEGER,
                track_id TEXT,
                download_time INTEGER,
                title TEXT,
                artist TEXT,
                duration INTEGER
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS search_cache (
                query_hash TEXT PRIMARY KEY,
                results TEXT,
                timestamp INTEGER
            )
        ''')
        # НОВАЯ ТАБЛИЦА: для хранения временных ссылок SoundCloud
        await db.execute('''
            CREATE TABLE IF NOT EXISTS soundcloud_urls (
                url_hash TEXT PRIMARY KEY,
                full_url TEXT,
                timestamp INTEGER
            )
        ''')
        await db.commit()
    print("🗄️ База данных инициализирована.")

async def save_user_track(user_id: int, track_id: str, info: dict):
    """Сохраняет информацию о скачанном треке."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO user_downloads (user_id, track_id, download_time, title, artist, duration) VALUES (?, ?, ?, ?, ?, ?)',
            (
                user_id, track_id, int(time.time()),
                info.get('title'), info.get('artist'), info.get('duration')
            )
        )
        await db.commit()

async def get_user_daily_downloads(user_id: int) -> int:
    """Считает количество треков, скачанных пользователем за последние 24 часа."""
    twenty_four_hours_ago = int(time.time()) - 86400
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT COUNT(*) FROM user_downloads WHERE user_id = ? AND download_time > ?',
            (user_id, twenty_four_hours_ago)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

# --- Функции для кэша поиска ---

async def get_cached_search(query_hash: str) -> list | None:
    """Получает результаты поиска из кэша БД, если они не устарели."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT results, timestamp FROM search_cache WHERE query_hash = ?',
            (query_hash,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                results, timestamp = row
                if time.time() - timestamp < CACHE_TTL:
                    return json.loads(results)
                else:
                    await db.execute('DELETE FROM search_cache WHERE query_hash = ?', (query_hash,))
                    await db.commit()
    return None

async def save_search_to_cache(query_hash: str, results: list):
    """Сохраняет результаты поиска в кэш БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR REPLACE INTO search_cache (query_hash, results, timestamp) VALUES (?, ?, ?)',
            (query_hash, json.dumps(results), int(time.time()))
        )
        await db.commit()

async def cleanup_expired_cache():
    """Периодическая очистка устаревшего кэша поиска в БД."""
    cutoff_time = int(time.time()) - CACHE_TTL
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('DELETE FROM search_cache WHERE timestamp < ?', (cutoff_time,))
        await db.commit()
        if cursor.rowcount > 0:
            print(f"🧹 Очистка кэша поиска: удалено {cursor.rowcount} устаревших записей.")

# --- НОВЫЕ ФУНКЦИИ: для кэша ссылок SoundCloud ---

async def save_soundcloud_url(url_hash: str, full_url: str):
    """Сохраняет соответствие хэша и полной ссылки SoundCloud в БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR REPLACE INTO soundcloud_urls (url_hash, full_url, timestamp) VALUES (?, ?, ?)',
            (url_hash, full_url, int(time.time()))
        )
        await db.commit()

async def get_soundcloud_url(url_hash: str) -> str | None:
    """Получает полную ссылку SoundCloud по хэшу, если она не устарела."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT full_url, timestamp FROM soundcloud_urls WHERE url_hash = ?',
            (url_hash,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                full_url, timestamp = row
                if time.time() - timestamp < CACHE_TTL:
                    return full_url
                else:
                    # Ссылка устарела, удаляем ее
                    await db.execute('DELETE FROM soundcloud_urls WHERE url_hash = ?', (url_hash,))
                    await db.commit()
    return None

async def cleanup_expired_soundcloud_urls():
    """Периодическая очистка устаревших ссылок SoundCloud в БД."""
    cutoff_time = int(time.time()) - CACHE_TTL
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('DELETE FROM soundcloud_urls WHERE timestamp < ?', (cutoff_time,))
        await db.commit()
        if cursor.rowcount > 0:
            print(f"🧹 Очистка ссылок SoundCloud: удалено {cursor.rowcount} устаревших записей.")