import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_TOKEN = os.getenv("ALERTS_API_TOKEN", "").strip()
POLL_SECONDS = 60

if not BOT_TOKEN:
    raise SystemExit("Укажи BOT_TOKEN в файле .env")
if not API_TOKEN:
    raise SystemExit("Укажи ALERTS_API_TOKEN в файле .env")

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

BASE_DIR = Path(__file__).resolve().parent
SUBSCRIBERS_FILE = BASE_DIR / "subscribers.json"
STATUS_FILE = BASE_DIR / "status.json"

# Порядок областей и коды статусов соответствуют документации alerts.in.ua.
OBLASTS = [
    "АР Крым", "Волынская область", "Винницкая область",
    "Днепропетровская область", "Донецкая область", "Житомирская область",
    "Закарпатская область", "Запорожская область", "Ивано-Франковская область",
    "г. Киев", "Киевская область", "Кировоградская область",
    "Луганская область", "Львовская область", "Николаевская область",
    "Одесская область", "Полтавская область", "Ровенская область",
    "г. Севастополь", "Сумская область", "Тернопольская область",
    "Харьковская область", "Херсонская область", "Хмельницкая область",
    "Черкасская область", "Черновицкая область", "Черниговская область"
]
CODE_LABELS = {
    "R": ("🔴", "Воздушная тревога"),
    "Y": ("🟡", "Предупреждение"),
    "P": ("🟠", "Частичная тревога"),
    "N": ("🟢", "Нет активной тревоги"),
    " ": ("⚪", "Нет данных"),
    "A": ("🔴", "Воздушная тревога"),
}

def read_subscribers():
    try:
        return set(json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return set()

def save_subscribers(items):
    SUBSCRIBERS_FILE.write_text(
        json.dumps(sorted(items), ensure_ascii=False, indent=2), encoding="utf-8"
    )

def normalize_status_payload(data):
    # API обычно возвращает JSON-строку с 27 символами.
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("active_air_raid_alerts", "data", "status"):
            if isinstance(data.get(key), str):
                return data[key]
    raise ValueError("Неожиданный формат ответа API")

async def fetch_statuses():
    url = "https://api.alerts.in.ua/v2/iot/active_air_raid_alerts_by_oblast.json"
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
    codes = normalize_status_payload(payload)
    if len(codes) < len(OBLASTS):
        raise ValueError(f"API вернуло {len(codes)} статусов вместо ожидаемых 27")
    result = []
    for name, code in zip(OBLASTS, codes):
        emoji, label = CODE_LABELS.get(code, ("⚪", "Нет данных"))
        result.append({"name": name, "code": code, "emoji": emoji, "status": label})
    return result

def format_statuses(statuses):
    active = [x for x in statuses if x["code"] in ("R", "Y", "P", "A")]
    if not active:
        return "По полученным данным активных тревог сейчас нет.\n\nПроверяй официальные оповещения."
    return "Текущие предупреждения:\n" + "\n".join(
        f'{x["emoji"]} {x["name"]} — {x["status"]}' for x in active
    )

async def refresh_loop():
    previous = None
    while True:
        try:
            statuses = await fetch_statuses()
            STATUS_FILE.write_text(json.dumps(statuses, ensure_ascii=False), encoding="utf-8")
            current = {x["name"]: x["code"] for x in statuses}
            if previous is not None:
                changed = []
                for item in statuses:
                    old = previous.get(item["name"])
                    if old != item["code"] and item["code"] in ("R", "Y", "P", "A"):
                        changed.append(f'{item["emoji"]} {item["name"]}: {item["status"]}')
                    elif old in ("R", "Y", "P", "A") and item["code"] == "N":
                        changed.append(f'🟢 {item["name"]}: отбой по данным API')
                if changed:
                    text = "Обновление статусов тревог:\n" + "\n".join(changed)
                    for chat_id in read_subscribers():
                        try:
                            await bot.send_message(int(chat_id), text)
                        except Exception as exc:
                            logging.warning("Не удалось отправить %s: %s", chat_id, exc)
            previous = current
        except Exception:
            logging.exception("Не удалось обновить статусы. Повтор через минуту.")
        await asyncio.sleep(POLL_SECONDS)

@dp.message(Command("start"))
async def start(message: Message):
    subscribers = read_subscribers()
    subscribers.add(message.chat.id)
    save_subscribers(subscribers)
    await message.answer(
        "Привет! Я показываю официальные статусы воздушных тревог по областям Украины.\n\n"
        "/alerts — текущие предупреждения\n"
        "/map — открыть локальную интерактивную карту на этом компьютере\n"
        "/stop — отключить уведомления\n\n"
        "Важно: данные могут поступать с задержкой. Не используй бота вместо официальных "
        "сирен и указаний служб."
    )

@dp.message(Command("stop"))
async def stop(message: Message):
    subscribers = read_subscribers()
    subscribers.discard(message.chat.id)
    save_subscribers(subscribers)
    await message.answer("Уведомления отключены. Чтобы включить снова, отправь /start.")

@dp.message(Command("alerts"))
async def alerts(message: Message):
    try:
        statuses = await fetch_statuses()
        await message.answer(format_statuses(statuses))
    except Exception:
        await message.answer("Не удалось получить данные. Проверь API-токен и подключение к интернету.")

@dp.message(Command("map"))
async def map_command(message: Message):
    await message.answer(
        "Интерактивная карта открывается на компьютере, где запущен бот:\n"
        "http://127.0.0.1:8080\n\n"
        "Если страница не открывается, запусти bot.py и попробуй ещё раз."
    )

async def map_data(request):
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = []
    return web.json_response(data)

async def map_page(request):
    return web.FileResponse(BASE_DIR / "map.html")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", map_page)
    app.router.add_get("/status.json", map_data)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8080)
    await site.start()
    logging.info("Карта доступна на http://127.0.0.1:8080")
    return runner

async def main():
    runner = await start_web_server()
    try:
        await asyncio.gather(
            dp.start_polling(bot),
            refresh_loop()
        )
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
