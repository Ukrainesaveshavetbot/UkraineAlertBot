import asyncio, json, logging, os
from pathlib import Path
from aiohttp import web, ClientSession, ClientTimeout
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
API_TOKEN=os.getenv("ALERTS_API_TOKEN","").strip()
PORT=int(os.getenv("PORT","8080"))
if not BOT_TOKEN or not API_TOKEN:
    raise RuntimeError("Задайте BOT_TOKEN і ALERTS_API_TOKEN у змінних середовища.")
logging.basicConfig(level=logging.INFO)
bot=Bot(BOT_TOKEN); dp=Dispatcher()
BASE=Path(__file__).parent
SUBS=BASE/"subscribers.json"
LATEST={"statuses":[],"error":"Очікування першого оновлення"}
NAMES=["АР Крим","Волинська область","Вінницька область","Дніпропетровська область","Донецька область","Житомирська область","Закарпатська область","Запорізька область","Івано-Франківська область","м. Київ","Київська область","Кіровоградська область","Луганська область","Львівська область","Миколаївська область","Одеська область","Полтавська область","Рівненська область","м. Севастополь","Сумська область","Тернопільська область","Харківська область","Херсонська область","Хмельницька область","Черкаська область","Чернівецька область","Чернігівська область"]
LABELS={"R":("🔴","Повітряна тривога"),"A":("🔴","Повітряна тривога"),"Y":("🟡","Попередження"),"P":("🟠","Часткова тривога"),"N":("🟢","Немає активної тривоги")," ":("⚪","Немає даних")}
def subscribers():
    try: return set(map(int,json.loads(SUBS.read_text(encoding="utf-8"))))
    except Exception: return set()
def save(ids): SUBS.write_text(json.dumps(sorted(ids)),encoding="utf-8")
async def fetch():
    url="https://api.alerts.in.ua/v2/iot/active_air_raid_alerts_by_oblast.json"
    async with ClientSession(timeout=ClientTimeout(total=20)) as s:
        async with s.get(url,headers={"Authorization":f"Bearer {API_TOKEN}"}) as r:
            r.raise_for_status(); data=await r.json(content_type=None)
    codes=data if isinstance(data,str) else (data.get("data") or data.get("active_air_raid_alerts")) if isinstance(data,dict) else None
    if not isinstance(codes,str) or len(codes)<27: raise ValueError("Невідомий формат відповіді API")
    return [{"name":n,"code":c,"emoji":LABELS.get(c,("⚪","Невідомо"))[0],"label":LABELS.get(c,("⚪","Невідомо"))[1]} for n,c in zip(NAMES,codes)]
def summary(rows):
    active=[x for x in rows if x["code"] in ("R","A","Y","P")]
    return "Поточні регіональні статуси:\n"+"\n".join(f'{x["emoji"]} {x["name"]} — {x["label"]}' for x in active) if active else "За останніми отриманими даними активних попереджень не відображено."
@dp.message(Command("start"))
async def start(m:Message):
    ids=subscribers(); ids.add(m.chat.id); save(ids)
    await m.answer("Вітаю! Я надсилаю інформаційні сповіщення про офіційні статуси тривог по областях.\n/start — підписка\n/alerts — статуси\n/map — карта\n/stop — вимкнути.\n\nДані можуть затримуватися. Дотримуйся офіційних сповіщень і вказівок служб.")
@dp.message(Command("stop"))
async def stop(m:Message):
    ids=subscribers(); ids.discard(m.chat.id); save(ids); await m.answer("Сповіщення вимкнено.")
@dp.message(Command("alerts"))
async def alerts(m:Message):
    await m.answer(summary(LATEST["statuses"]) if LATEST["statuses"] else "Поки немає свіжих даних.")
@dp.message(Command("map"))
async def map_cmd(m:Message):
    url=os.getenv("PUBLIC_MAP_URL","").strip()
    await m.answer("Карта: "+url if url else "Відкрий головну адресу свого вебсервісу Render.")
async def poll():
    old=None
    while True:
        try:
            rows=await fetch(); LATEST.update(statuses=rows,error=None)
            now={x["name"]:x["code"] for x in rows}
            if old is not None:
                changes=[]
                for x in rows:
                    prev=old.get(x["name"]); cur=x["code"]
                    if prev!=cur and cur in ("R","A","Y","P"): changes.append(f'{x["emoji"]} {x["name"]}: {x["label"]}')
                    elif prev in ("R","A","Y","P") and cur=="N": changes.append(f'🟢 {x["name"]}: статус скасовано за даними API')
                if changes:
                    msg="Оновлення регіональних статусів:\n"+"\n".join(changes)
                    for uid in subscribers():
                        try: await bot.send_message(uid,msg)
                        except Exception: logging.exception("Не вдалося надіслати повідомлення")
            old=now
        except Exception as e:
            LATEST["error"]=str(e); logging.exception("Помилка оновлення API")
        await asyncio.sleep(90)
async def index(req): return web.FileResponse(BASE/"map.html")
async def status(req): return web.json_response(LATEST)
async def health(req): return web.json_response({"ok":True})
async def main():
    app=web.Application(); app.router.add_get("/",index); app.router.add_get("/api/status",status); app.router.add_get("/health",health)
    runner=web.AppRunner(app); await runner.setup(); await web.TCPSite(runner,"0.0.0.0",PORT).start()
    try: await asyncio.gather(dp.start_polling(bot),poll())
    finally: await runner.cleanup(); await bot.session.close()
if __name__=="__main__": asyncio.run(main())
